# Scam Guard sheet

Command: `python scripts/guard_eval.py`. Text only, plain rules, no audio. 16 scam scripts and 16 ordinary calls.

| Metric | Result (n, 95% range) |
|---|---|
| Scam scripts that reached red | 15/16 (94%, 72 to 99%) |
| Ordinary calls that reached red (false alarm) | 1/16 (6%, 1 to 28%) |
| Ordinary calls that reached amber only | 0/16 (0%, 0 to 19%) |
| Chunks until red on caught scams (median, slowest) | 2, 3 |

## Detection by type

| Type | Caught |
|---|---|
| bank_or_code_request | 4/4 (100%, 51 to 100%) |
| digital_arrest | 4/4 (100%, 51 to 100%) |
| family_emergency | 2/2 (100%, 34 to 100%) |
| payment_demand | 2/3 (67%, 21 to 94%) |
| remote_access_scam | 3/3 (100%, 44 to 100%) |

## Detection by language

| Language | Caught |
|---|---|
| en | 8/9 (89%, 57 to 98%) |
| hi | 3/3 (100%, 44 to 100%) |
| hinglish | 4/4 (100%, 51 to 100%) |

## Every miss and every false alarm

- MISSED scam `s13` (payment_demand, en)
- FALSE ALARM on `n16` (delivery_otp, en) at chunk 2

## Limits

- The rules and this set were written by the same people at the same time, and the rules were adjusted after the first run missed scripts in this same set (first run 11/16 caught; added threat phrases and two red combinations). These numbers are optimistic. No clean held-out part exists.
- Text only: real calls come through a speaker and a transcriber that makes errors (especially in Hindi and Hinglish). Not measured here; see package SG6.
- One labeller. Hindi and Hinglish phrases and scripts are our drafts, not reviewed by a native speaker (SG7).
- The delivery-OTP call (n16) is a real gray case the rules cannot tell from a scam; it is kept on purpose.
