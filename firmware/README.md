# Firmware

Two boards, because one cannot do both jobs.

```
  5 sensors ──► Arduino Uno ──serial──► Raspberry Pi ──LoRaWAN──► gateway
                (sampling,              (framing, airtime
                 filtering,              budget, radio)
                 calibration)
```

## Why the split

The thesis specifies an Arduino Uno managing the sensors and a Raspberry Pi
centralising and transmitting. That division turns out to be forced rather than
stylistic: **a LoRaWAN stack does not fit on an ATmega328P.** The MCCI LMIC
library's own OTAA example consumes about 83% of the Uno's 32 KB of flash and
overruns its 2 KB RAM budget for globals before any application code is added.
Fitting it means disabling Class B beacon tracking and ping slots and then
fighting for every byte.

So the Uno does what an 8-bit AVR is good at - reading analogue sensors on a
tight, predictable loop - and hands framed readings to the Pi over serial. The
Pi owns the radio, the airtime budget and the join state, none of which are
memory-constrained there.

## Wire format

`arduino_node` emits one ASCII line per sample. The Pi parses it, applies no
interpretation of its own, and packs the 13-byte LoRaWAN payload using
**the same codec the cloud decodes with** - `earthguardian.edge.lorawan`. There
is exactly one definition of the wire format in this repository, and both ends
import it. A payload format defined twice is a payload format that will
disagree with itself.

## What is real here and what is not

The sketch and the gateway are written to be correct, not to be theatre: the
calibration, the filtering, the framing and the airtime arithmetic are all
what the deployed system would run. What is missing is the radio driver call
itself, which is stubbed behind `Radio.send()` - there is no hardware attached
to this repository, and a fake `RadioLib` call that silently returns success
would be worse than an obvious stub.
