# Raksha recognition sheet

Command: `python scripts/raksha_eval.py`. 20 messages that should trigger, 20 near-misses that must not.

| Metric | Result (n, 95% range) |
|---|---|
| Messages that should trigger and did | 19/20 (95%, 76 to 99%) |
| Near-misses that triggered (false triggers) | 0/20 (0%, 0 to 16%) |

## Misses and false triggers

- MISSED `t16`: he has my pics and is saying he will send them to everyone

## Limits

- Written by us, one labeller, and the word list was written by the same people (optimistic). The first run caught 16/20; phrases were added after seeing the misses on this same set. Short, indirect, misspelt and other-language wording will be missed.
- A miss means the bot treats the message like any other: the person is not harmed by a missed trigger but gets no help; the `/help_me` command always works.
- No 'are you asking about private images?' question exists yet for unsure messages (the plan asked for one).
