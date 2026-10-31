# Evaluation on the claim sheet, 2026-10-31

A small hand-made sheet (`eval/claims.yaml`), **not an independent benchmark**: the truth labels are widely documented facts and widely debunked rumours chosen by us.
Each claim was sent to `POST /verify` on the running prototype, with live Gemini, Fact Check and the other sources of that moment. One run each; results can differ on another day or after a fact-check is published.

## Summary

- Claims run: 22 of 22 (0 errors)
- **True claims wrongly refuted: 0 of 8**
- **False claims wrongly supported: 0 of 10**
- False claims refuted: 7 of 10; marked misleading: 0; cannot tell: 3
- True claims supported: 0 of 8; cannot tell: 8
- Claims nobody can have checked: cannot tell 4 of 4; any decided label here (0) needs a look

Reading it: the system only decides when a trusted source has checked the very same claim, so a high 'cannot tell' rate on true everyday facts is expected, not a failure. A decided label on the wrong side is the failure that counts.

## Every claim

| id | truth | claim | result | confidence | ledger | last reasons |
|---|---|---|---|---|---|---|
| f01 | false | COVID-19 vaccines contain microchips | REFUTED | medium | 13 | judged the same claim, rated 'Missing context' -> mixed; trusted source contradicts the claim |
| f02 | false | 5G towers spread the coronavirus | REFUTED | medium | 17 | judged to review a different claim; trusted source contradicts the claim |
| f03 | false | Drinking hot water cures coronavirus | UNVERIFIABLE_YET | low | 18 | judged to review a different claim; no trusted review of this claim |
| f04 | false | Vaccines cause autism | REFUTED | medium | 19 | judged the same claim, rated 'False' -> refutes; trusted source contradicts the claim |
| f05 | false | Humans only use 10 percent of their brains | REFUTED | medium | 20 | judged the same claim, rated 'False' -> refutes; trusted source contradicts the claim |
| f06 | false | The Great Wall of China is visible from space with the naked eye | UNVERIFIABLE_YET | low | 21 | judged to review a different claim; no trusted review of this claim |
| f07 | false | Eating garlic prevents coronavirus | REFUTED | medium | 22 | judged to review a different claim; trusted source contradicts the claim |
| f08 | false | Drinking bleach cures COVID-19 | UNVERIFIABLE_YET | low | 23 | judged to review a different claim; no trusted review of this claim |
| f09 | false | Smoking does not cause cancer | REFUTED | medium | 24 | judged to review the opposite claim; trusted source contradicts the claim |
| f10 | false | The COVID-19 vaccine changes your DNA | REFUTED | high | 25 | trusted source contradicts the claim; unmapped ratings stay visible and decide nothing: ['Social media users are citing a study from Sweden that was published in February to push the unproven theory that mRNA COVID-19 vaccines permanently alter recipients’ DNA', 'Some of the Covid-19 vaccine candidates are RNA vaccines. These don’t alter your DNA, they comprise of mRNA that gives the body instructions on how to make the proteins on the surface of the virus.'] |
| t01 | true | Smoking causes lung cancer | UNVERIFIABLE_YET | low | 4 | judged to review a different claim; no trusted review of this claim |
| t02 | true | The Earth orbits the Sun | NO_CLAIM |  |  |  |
| t03 | true | Water boils at 100 degrees Celsius at sea level | UNVERIFIABLE_YET | low | 26 | no trusted review of this claim |
| t04 | true | New Delhi is the capital of India | UNVERIFIABLE_YET | low | 27 | no trusted review of this claim |
| t05 | true | Mount Everest is the highest mountain above sea level | UNVERIFIABLE_YET | low | 28 | judged to review a different claim; no trusted review of this claim |
| t06 | true | Measles is a vaccine-preventable disease | UNVERIFIABLE_YET | low | 29 | judged to review a different claim; no trusted review of this claim |
| t07 | true | Handwashing with soap reduces the spread of infections | UNVERIFIABLE_YET | low | 30 | no trusted review of this claim |
| t08 | true | Vaccines save millions of lives every year | UNVERIFIABLE_YET | low | 31 | judged to review a different claim; no trusted review of this claim |
| u01 | unknown | Prime Minister Modi was assassinated this morning | UNVERIFIABLE_YET | low | 32 | no trusted review of this claim |
| u02 | unknown | A bridge in Mumbai collapsed an hour ago and 40 people died | UNVERIFIABLE_YET | low | 33 | no trusted review of this claim |
| u03 | unknown | The RBI has banned all UPI payments from tomorrow | UNVERIFIABLE_YET | low | 34 | no trusted review of this claim |
| u04 | unknown | Schools in Delhi will be closed for the next two weeks | UNVERIFIABLE_YET | low | 35 | no trusted review of this claim |
