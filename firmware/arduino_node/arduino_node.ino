/*
 * Earth Guardian - sensing node
 *
 * Reads the five environmental channels the system is specified around, filters
 * them, and hands a framed reading to the Raspberry Pi over serial. The Pi owns
 * the LoRaWAN radio; see ../README.md for why that split is not optional on an
 * ATmega328P.
 *
 * Board:   Arduino Uno (ATmega328P)
 * Sensors: capacitive soil moisture (A0), DHT22 (D2), LDR divider (A1),
 *          analogue pH probe (A2), MQ-135 (A3), battery divider (A4)
 *
 * The soil moisture pin matches the prototype schematic in
 * docs/original-2024/earth_guardian_circuit_diagram.pdf.
 */

#include <DHT.h>

// ---------------------------------------------------------------------------
// Pins
// ---------------------------------------------------------------------------
const uint8_t PIN_SOIL     = A0;
const uint8_t PIN_LDR      = A1;
const uint8_t PIN_PH       = A2;
const uint8_t PIN_MQ135    = A3;
const uint8_t PIN_BATTERY  = A4;
const uint8_t PIN_DHT      = 2;
const uint8_t PIN_STATUS   = 9;   // the LED on the schematic

DHT dht(PIN_DHT, DHT22);

// ---------------------------------------------------------------------------
// Calibration
//
// A capacitive probe reports capacitance, not water. The two-point calibration
// below (dry in air, submerged in water) linearises the ADC into a 0-1 wetness
// index - but that index is NOT volumetric water content: the relationship
// between the two depends on the soil's texture and salinity, and a probe
// calibrated in tap water reads several percent off in clay.
//
// The node deliberately does not pretend otherwise. It transmits the linearised
// index and lets the cloud recover the soil-specific mapping from the plot's
// own behaviour, which it can do because it sees a whole season and the node
// sees one reading.
// ---------------------------------------------------------------------------
const int SOIL_ADC_DRY = 620;   // probe in air
const int SOIL_ADC_WET = 285;   // probe in water

// pH probe: two buffer solutions, the standard field procedure.
const float PH_CAL_SLOPE  = -0.0178f;  // pH units per ADC count
const float PH_CAL_OFFSET = 15.94f;

// ---------------------------------------------------------------------------
// Sampling
// ---------------------------------------------------------------------------
const uint8_t  MEDIAN_SAMPLES  = 7;     // odd, so the median is a real reading
const uint16_t SAMPLE_PERIOD_S = 900;   // 15 minutes

/*
 * Median of a handful of reads. A mean would be the obvious choice and the
 * wrong one: the failure mode of these analogue sensors is an occasional wild
 * single reading - a loose ground, a switching transient from the pump relay -
 * and a mean carries that into the payload while a median discards it.
 */
static int readAnalogMedian(uint8_t pin) {
  int samples[MEDIAN_SAMPLES];
  for (uint8_t i = 0; i < MEDIAN_SAMPLES; i++) {
    samples[i] = analogRead(pin);
    delay(5);
  }
  for (uint8_t i = 1; i < MEDIAN_SAMPLES; i++) {
    int key = samples[i];
    int8_t j = i - 1;
    while (j >= 0 && samples[j] > key) {
      samples[j + 1] = samples[j];
      j--;
    }
    samples[j + 1] = key;
  }
  return samples[MEDIAN_SAMPLES / 2];
}

static float soilWetnessIndex() {
  int raw = readAnalogMedian(PIN_SOIL);
  float index = (float)(SOIL_ADC_DRY - raw) / (float)(SOIL_ADC_DRY - SOIL_ADC_WET);
  return constrain(index, 0.0f, 1.0f);
}

static float soilPh() {
  int raw = readAnalogMedian(PIN_PH);
  float ph = PH_CAL_SLOPE * (float)raw + PH_CAL_OFFSET;
  return constrain(ph, 3.0f, 10.0f);
}

/*
 * LDR divider to illuminance. The curve is a power law in the log domain and
 * the constants are specific to the divider resistor; this is a photometric
 * estimate with tens of percent of error, which is why the cloud treats
 * luminosity as a proxy for solar radiation rather than a measurement of it.
 */
static float illuminance() {
  int raw = readAnalogMedian(PIN_LDR);
  if (raw <= 0) return 0.0f;
  float voltage = raw * (5.0f / 1023.0f);
  if (voltage >= 4.99f) return 100000.0f;
  float resistance = 10000.0f * (5.0f - voltage) / voltage;
  return 255000.0f * pow(resistance, -1.42f);
}

/*
 * MQ-135 in ppm-equivalent. The sensor needs a burn-in and its resistance moves
 * with temperature and humidity, so this figure is an index, not a
 * concentration. The cloud compares it against the node's own baseline rather
 * than against any published limit.
 */
static float airQualityPpm(float temperatureC, float humidityPct) {
  int raw = readAnalogMedian(PIN_MQ135);
  float voltage = raw * (5.0f / 1023.0f);
  if (voltage <= 0.01f) return 0.0f;
  float rs = 10000.0f * (5.0f - voltage) / voltage;
  const float R0 = 76000.0f;             // clean-air resistance, from burn-in
  float ratio = rs / R0;
  float ppm = 116.6021f * pow(ratio, -2.769034f);
  // First-order compensation; the datasheet curves are drawn at 20 C / 65% RH.
  float correction = 1.0f + 0.018f * (20.0f - temperatureC) + 0.004f * (65.0f - humidityPct);
  return constrain(ppm * correction, 0.0f, 2000.0f);
}

static float batteryPercent() {
  int raw = readAnalogMedian(PIN_BATTERY);
  float voltage = raw * (5.0f / 1023.0f) * 2.0f;   // 1:1 divider on an 18650
  float pct = (voltage - 3.20f) / (4.20f - 3.20f) * 100.0f;
  return constrain(pct, 0.0f, 100.0f);
}

void setup() {
  Serial.begin(9600);
  pinMode(PIN_STATUS, OUTPUT);
  dht.begin();
  // The MQ-135 heater needs to stabilise before its readings mean anything.
  delay(20000);
  Serial.println(F("# earth-guardian node v1 ready"));
}

void loop() {
  digitalWrite(PIN_STATUS, HIGH);

  float temperature = dht.readTemperature();
  float humidity    = dht.readHumidity();
  if (isnan(temperature) || isnan(humidity)) {
    // A failed DHT read is common and must not poison the frame. Say so and
    // let the gateway decide; silently substituting the last value would hide
    // a dying sensor for months.
    Serial.println(F("# dht read failed"));
    temperature = NAN;
    humidity = NAN;
  }

  float wetness = soilWetnessIndex();
  float lux     = illuminance();
  float ph      = soilPh();
  float air     = airQualityPpm(isnan(temperature) ? 20.0f : temperature,
                                isnan(humidity) ? 65.0f : humidity);
  float battery = batteryPercent();

  // One line, fixed field order, so the gateway parser stays trivial.
  Serial.print(F("EG1,"));
  Serial.print(wetness, 4);    Serial.print(',');
  Serial.print(temperature, 2); Serial.print(',');
  Serial.print(humidity, 2);   Serial.print(',');
  Serial.print(lux, 1);        Serial.print(',');
  Serial.print(ph, 2);         Serial.print(',');
  Serial.print(air, 1);        Serial.print(',');
  Serial.println(battery, 1);

  digitalWrite(PIN_STATUS, LOW);
  delay((unsigned long)SAMPLE_PERIOD_S * 1000UL);
}
