# Forensics build map: pulse-based video audit, plus image and audio

Instructions for whoever writes the `forensics` module. Follow the phases in order. Each phase lists the files to create, the
steps, and a **done when** check. Time figures are guesses, not measurements. Anything marked _measure_ must come from a script,
not from memory.

**Scope note (merged plan).** The video pulse path is phases F1 to F10. The audio track is phases A0 to A9 in section 5A. The
image track is phases I0 to I8 in section 5B. Because forwarded video is heavily compressed and pretrained audio detectors
exist, the merged master map makes **audio the only Tier 1 detector**. The **pulse path is demoted to Tier 2** (gate and panel
only; its classifier, evaluation and neural branch are Tier 3) to pay for reverse-image provenance, which is now Tier 1 and is
described in the [provenance map](BUILD_MAP_PROVENANCE.md). The image track stays Tier 2, with **TruFor (FO-24) the first item
promoted** when Tier 1 is green. Media verdict labels here follow the merged set: `SYNTHETIC`, `EDITED`, `OUT_OF_CONTEXT`,
`AUTHENTIC_IN_CONTEXT`, `INCONCLUSIVE`. Provenance outranks every score in this file.

## 1. Where this sits

```
 upload ─► decode ─► QUALITY GATE ─┬─ fails ──► DetectorResult(abstained=True, reason)
                                   └─ passes ─► features ─► classifier ─► DetectorResult(score, evidence)
                                                                              │
                                                              fusion (never decisive from pulse alone)
```

The video path measures a pulse from skin colour (rPPG), checks whether it is consistent across face regions, and feeds that to
a classifier. Two facts from the source material shape every decision below:

- Compression and movement destroy the pulse signal, and the original FakeCatcher authors' own false alarms came from movement
  and uneven light. So **the quality gate is the product**, and abstaining is a correct answer.
- Good face swaps can now copy the pulse from the real video they were made from. So **a pulse never proves a video real.** It
  can support authenticity, or raise suspicion when its quality is good, and fusion decides.

## 2. The three resources, and exactly how to use each

| Resource | Role in our build | Do | Do not |
|---|---|---|---|
| **FakeCatcher paper** (arXiv 1901.02212) and its Deep Fakes Dataset page | The method we reproduce in simplified form, and a possible test set | Read the introduction, Section 4.4 and Section 7 first. Reproduce the idea: skin ROIs, per-frame colour averages, spectral and coherence features across regions, a trained classifier. Check on the project page whether the dataset can be downloaded | Expect public code; none was found. Do not quote its 96% figure. The same paper reports about 91% on its harder set |
| **rPPG-Toolbox** (code, arXiv 2210.00716) | An **independent yardstick**, run in a separate environment | Use it to benchmark heart-rate estimation on UBFC-rPPG and compare against our own implementation. Read its dataset loaders and `unsupervised_methods/` for how POS and CHROM are structured | Do not vendor it into `src/`. Its licence is the Responsible AI Source Code License v1.1; read the restrictions and the distribution terms before using any part of it in the submission |
| **DeepFakesON-Phys** (code, arXiv 2010.00400) | A **reference design** for an optional neural branch, and an optional external baseline | Read its two scripts to understand the preprocessing (raw normalised frames plus difference frames) and per-frame scoring. Use the paper to design our own two-branch model | GitHub shows no licence for this repository, so treat the code and the `.h5` weights as not ours to copy or ship. Do not put them in `src/` or in the product. If you want them as a comparison baseline, ask the organisers first |

The event rules say reuse of open-source libraries is allowed, but core logic and AI models must be integrated and built during
the event window. So: **write the pulse extraction, features and classifier yourselves, and train every model that ships.**

## 3. Day-0 checklist (do before any code)

Dataset access is the slowest step. Start the requests first.

| Item | Why | Action |
|---|---|---|
| UBFC-rPPG | Real video with a recorded pulse. Needed for heart-rate validation | Download from its project page; the toolbox expects `data/UBFC-rPPG/subjectN/vid.avi` and `ground_truth.txt` |
| FaceForensics++ | Real and fake pairs from several generators | Submit the access form; the PDF notes about 10 GB |
| Celeb-DF v2, DFDC | Harder, more current fakes; used by the DeepFakesON-Phys paper | Check each dataset's terms and request access |
| FakeCatcher Deep Fakes Dataset | Original authors' set | Check the project page |
| WSL2 | rPPG-Toolbox's setup script targets Linux and macOS; its README suggests WSL on Windows | `wsl --install`, then follow the Toolbox steps inside it |
| ffmpeg and ffprobe | Re-encoding sweeps and reading codec and bitrate | Install and confirm both are on the path |
| MediaPipe Face Landmarker model file | Face landmarks | Download the `.task` model; store its path in config, not code |

Record, for every dataset: licence terms, number of subjects, whether skin-tone or Fitzpatrick labels exist. You will need that
for the slice report.

## 4. The contract every detector follows

Create `src/satyasutra/forensics/base.py` first and make the rest conform.

```python
@dataclass(frozen=True)
class QualityReport:
    passed: bool
    reasons: list[str]          # empty when passed; human-readable when not
    measures: dict[str, float]  # the raw numbers behind the decision

@dataclass(frozen=True)
class DetectorResult:
    detector: str               # e.g. "video.pulse"
    version: str                # git sha of the code plus model sha256
    abstained: bool
    score: float | None         # P(manipulated); None when abstained
    quality: QualityReport
    evidence: dict              # plots data, per-window scores, feature values

class Detector(Protocol):
    name: str
    def assess_quality(self, media: MediaRef) -> QualityReport: ...
    def analyze(self, media: MediaRef) -> DetectorResult: ...
```

Rules, each with a unit test:
1. `analyze` calls `assess_quality` first. If it did not pass, return `abstained=True, score=None`. A test asserts no score
   appears with a failed gate.
2. Every threshold is read from `config/thresholds.yaml` by key, never written in code. The file records which model hash and
   calibration run each value belongs to.
3. The detector never raises on bad input. It returns an abstention with a reason (unreadable file, no face, too short).

## 5. Phases

### F1: Decode and timebase (about 2 h)
Files: `forensics/video/decode.py`, `tests/unit/test_decode.py`.
1. Open with `cv2.VideoCapture`. Read the **real** frame rate with `cap.get(cv2.CAP_PROP_FPS)`. Every beats-per-minute number
   depends on it. If it is 0, NaN or absurd, abstain with a reason.
2. Also read each frame's timestamp (`cap.get(cv2.CAP_PROP_POS_MSEC)`). Phone and re-encoded clips often have variable frame
   rate. Resample the colour traces to a uniform grid by interpolating on those timestamps.
3. Use `ffprobe` to read codec, bitrate, resolution and duration into the quality measures.
4. Return frames lazily in windows; never load a whole video into memory.
5. Duration check: frequency resolution is 1 divided by the window length, so 10 s separates beats about 6 per minute apart and
   30 s about 2 (from the PDF). Set the minimum window length in config.

Done when: `pytest tests/unit/test_decode.py` passes for a constant-rate file, a variable-rate file (made with ffmpeg in the test)
and a corrupt file, which must abstain.

### F2: Face and skin traces (about 3 h)
Files: `forensics/video/face.py`, `forensics/video/roi.py`.
1. Run the MediaPipe Face Landmarker per frame in video mode. Keep the 478 landmarks.
2. Define ROIs as lists of landmark indices in `config/roi.yaml`: forehead, left cheek, right cheek. Find the indices by drawing
   the landmark numbers on one frame and reading them off; commit the debug image to `docs/media/`.
3. Fill each ROI polygon, erode it a few pixels so it stays on skin, and optionally drop pixels outside a skin-colour range.
4. Per frame and per ROI, output the mean R, G and B. Result: an array `(frames, rois, 3)` plus a per-frame `face_found` flag.
5. Record the face crop's pixel size and the frame-to-frame landmark displacement. These feed the gate.
6. Gaps: interpolate over short runs of missing faces (maximum run length in config); a window with a longer run is rejected.

Done when: `scripts/debug_roi.py <video>` writes an overlay image showing the three ROIs on the skin, and the trace array has no
NaN in an accepted window.

### F3: Pulse extraction (about 4 h)
Files: `forensics/video/rppg.py`, `tests/unit/test_rppg.py`.
Implement three methods yourselves, from their papers, as pure functions `(rgb: ndarray[T,3], fs: float) -> ndarray[T]`:
1. `green`: the green channel, detrended. Verkruysse 2008 is the reference. This is the baseline.
2. `chrom`: de Haan and Jeanne 2013.
3. `pos`: Wang et al. 2016, with the sliding-window overlap-add.
Then a shared post-step:
4. `scipy.signal.detrend`, then `butter(order, [lo, hi], btype='bandpass', fs=fs, output='sos')` and `sosfiltfilt`. Start with
   0.7 to 3.0 Hz as in the PDF; keep the band in config.
5. `numpy.fft.rfft` and `rfftfreq`, with zero-padding for a smoother peak, strongest bin inside the band, times 60 gives beats
   per minute. Also return the peak's power and the spectrum.
6. Unit tests with a **synthetic** sine at a known rate plus noise: the estimate must land within a tolerance. This is a test of
   the maths and may use generated signals.

Done when: all three methods pass the synthetic tests, and on one clip of a teammate's face the plotted band-passed trace looks
like a fairly regular wave and the tallest spike sits near the pulse counted at the wrist.

### F4: Validate against ground truth and against the Toolbox (about 4 h)
Files: `scripts/eval_hr.py`, `docs/hr_validation.md`.
1. Run your `green`, `chrom` and `pos` over every UBFC-rPPG subject. Read `ground_truth.txt` and compute the reference heart rate
   per window.
2. Report mean absolute error in beats per minute, per method, with a confidence interval over subjects.
3. In WSL, set up the Toolbox: `bash setup.sh conda` (or `uv`), then activate the environment. Run its unsupervised benchmark:
   `python main.py --config_file ./configs/infer_configs/UBFC-rPPG_UNSUPERVISED.yaml`. That filename exists in the
   repository. The Toolbox README spells it differently in one place (`UBFC_UNSUPERVISED.yaml`), so copy the name from the
   `configs/infer_configs` folder, not from the text.
4. Compare your POS and CHROM numbers with the Toolbox's on the same subjects. They will not match exactly, because ROI choice
   and windowing differ. Write down the gap and the reasons. A large unexplained gap means a bug in your code.
5. Do not use this as the accuracy claim for deepfakes. It only shows the pulse extraction works.

Done when: `docs/hr_validation.md` holds a table of your MAE against the Toolbox's, generated by `scripts/eval_hr.py`.

### F5: Quality gate, calibrated not guessed (about 5 h)
Files: `forensics/video/quality.py`, `scripts/calibrate_quality_gate.py`, `config/thresholds.yaml`.
1. Gate measures: face-found fraction; face crop size; landmark motion; brightness variance; codec, bitrate and a blockiness
   proxy; and the pulse **SNR** of the best ROI. For SNR, follow the definition the Toolbox uses in its `evaluation/` code so
   numbers are comparable.
2. Build a degradation set from UBFC-rPPG videos: re-encode each at a ladder of quality levels with ffmpeg (`-crf`), at a few
   downscaled sizes, and with a synthetic shake. Keep the originals' ground truth.
3. For each degraded copy, run the heart-rate estimate and record error against the reference.
4. Choose thresholds so that **clips the gate passes have heart-rate error below a target set in config.** Write the chosen
   values, the date, the code version and the data used into `thresholds.yaml`.
5. Plot error against quality level and the fraction of clips abstained. This curve is the honest picture of what compression
   does; show it in the demo.

Done when: `scripts/calibrate_quality_gate.py` regenerates the thresholds file, and a test shows a heavily degraded clip
abstains while a clean one passes.

### F6: Consistency features and classifier (about 6 h)
Files: `forensics/video/features.py`, `forensics/video/classifier.py`, `scripts/train_video.py`.
1. Windows: slide a window of length W with a step S (both in config) over the traces. Skip windows that fail the gate.
2. Per accepted window compute, from band-passed signals of the three ROIs:
   - heart rate per ROI, and the standard deviation across ROIs;
   - pairwise `scipy.signal.coherence` evaluated at the global peak frequency; mean and minimum;
   - pairwise Pearson correlation, at zero lag and at the best small lag;
   - peak sharpness (top peak over second peak) and spectral flatness inside the band;
   - the same measures from a wider band (the PDF notes FakeCatcher keeps up to 14 Hz because the leftover signal helps).
3. Label each window with its video's label. Never use window-level splitting.
4. Model: logistic regression first, then gradient boosting. Calibrate probabilities on a held-out fold (isotonic).
5. Splits with `GroupKFold`, grouped so **every clip of one person, and every fake made from one real video, stay on one side**.
6. Video-level score: the mean logit over accepted windows. Require at least K accepted windows (config), otherwise abstain.
7. Save the model with its sha256 and the feature list; put that hash in `thresholds.yaml`.

Done when: `scripts/train_video.py` reports cross-validated AUC with a confidence interval, and writes the model plus its hash.

### F7: Evaluation that does not flatter us (about 4 h)
Files: `scripts/eval_forensics.py`, results into `docs/claim-sheet.md`.
Run all of these and report each separately:
1. **Grouped cross-validation** inside one dataset (F6).
2. **Leave-one-generator-out** on FaceForensics++: train without one manipulation method and test on it.
3. **Cross-dataset**: train on one dataset, test on another (for example FaceForensics++ to Celeb-DF).
4. **Compression slices**: re-encode the test set at the F5 quality ladder and report each level.
5. **Selective accuracy**: plot accuracy against the fraction answered (a risk-coverage curve), and report the abstention rate.
6. **Skin tone**: report by group only where the dataset provides labels; otherwise state that no breakdown exists.
7. Metrics: AUC, true-positive rate at 1% false-positive rate, and the false-fake rate on real videos (the BBC failure mode).

**Decision gate.** If the cross-dataset result is not clearly above chance (confidence interval excludes 0.5), do not claim the
pulse detector works. Keep pulse as quality-gated supporting evidence, shown in the UI, and move the time to image and audio.

### F8: Optional neural branch (decision gate; only if F7 is clearly positive; about 10 h)
Files: `forensics/video/neural/model.py`, `train.py`, `export_onnx.py`.
1. Design after the DeepFakesON-Phys paper, in PyTorch: two inputs per frame, the normalised frame (appearance) and the
   normalised difference between consecutive frames (motion). Two small convolutional branches; the appearance branch produces
   attention masks that weight the motion branch; a small head outputs real or fake. Check the paper's section on the
   architecture before fixing layer sizes, and write the layers yourselves.
2. Train from scratch or from a Toolbox-style heart-rate model **that you train yourselves**; do not import their weights into
   the product.
3. Preprocess like their script: crop the face, resize, normalise, compute frame differences. Write this yourselves.
4. Score per frame, aggregate over time windows (their README says per-frame scores are meant to be combined by temporal
   windows), then run the same F7 protocol. Report it beside the F6 classifier.
5. Export to ONNX; confirm output matches PyTorch within tolerance on a test batch.
6. Optional baseline: with organiser approval, run the published DeepFakesON-Phys scripts on the same test clips and report the
   comparison, clearly labelled as an external baseline.

Done when: the neural branch has a row in the claim sheet from the same protocol as F6, or the gate decided to skip it.

### F9: Image detectors
Superseded by the image track, section 5B (phases I0 to I8), which uses pretrained detectors and TruFor. The audio detector has
its own track, section 5A. Both are described there; this heading is kept so older references still resolve.

### F10: Service, UI and drills (about 6 h)
Files: `forensics/service.py`, `api/routes/forensics.py`, `ui/forensics/`, `tests/drills/`.
1. Run detectors in a worker process pulling jobs from the queue, so a slow video never blocks the API.
2. Endpoint `POST /forensics/video` (and `/image`, `/audio`) accepts an upload with size and type limits and returns the
   `DetectorResult` JSON plus an evidence payload.
3. **Pulse panel** in the UI: per-ROI band-passed traces, the spectrum with the chosen peak, the per-window score timeline,
   and the **gate verdict with reasons**.
4. **Live heart-rate check**: record 15 s from the browser camera, upload, show the estimated pulse and the gate state. A judge
   can compare it with their own pulse. It proves the pipeline is live. Label it as a heart-rate demo, not a deepfake test.
5. **Compression slider**: re-encode the uploaded video at several quality levels on demand and show the gate flipping from
   pass to abstain. This demonstrates the limitation instead of hiding it.
6. Drills: no face; multiple faces (pick the largest, report it); face partly out of frame; very short clip; very dark clip;
   audio-only upload to the video endpoint; corrupt container; a file over the size limit.

Done when: every drill returns a clean abstention or an error message with no crash, and the live check runs twice in a row.

## 5A. Audio track (phases A0 to A9)

Voice notes are a main channel for forwards, and voice cloning is cheap, so audio is the Tier 1 detector. Pretrained detectors
exist, so the work is **not** training a detector from nothing. The work is making them trustworthy on compressed, forwarded,
Indian-language audio: a degradation pipeline, calibration, a quality gate, an ensemble that can disagree, and an honest
evaluation.

### The audio resources

| Resource | What it is | Licence shown | Role | Cautions |
|---|---|---|---|---|
| **Spectra-AASIST** (lab260) | Raw audio, then a Wav2Vec2 encoder, an MLP projection and an AASIST classifier; outputs bonafide or spoof. PyTorch, ONNX and safetensors files exist | Apache-2.0 | **First choice** for the main environment: permissive licence, ONNX | The card's own EERs are self-reported, and two figures for the same ASVspoof 2019 LA set differ between its metadata (0.159) and its arena badge (0.38). Read its training-data section: any test set it was trained on is not a held-out test. Your notes say the older AASIST3 model from the same group is deprecated; do not use it |
| **W2V2-AASIST** (benchmark wrapper, ONNX; original code and weights by Tak et al.) | wav2vec 2.0 front end with an AASIST back end | MIT (wrapper and original repository) | Standard strong baseline; use the ONNX wrapper | Its card shows EER near 0.22% on ASVspoof 2019 LA but 38.57% on CD-ADD and 46.12% on SONAR, close to chance. That is the cross-dataset gap you must expect. The original repository needs Python 3.7, PyTorch 1.8 and a pinned fairseq folder, with weights on a shared drive; avoid it |
| **AntiDeepfake, Wav2Vec2-Large NDA** (NII Yamagishi Lab) | wav2vec 2.0 large with a linear classifier, post-trained on about 18k hours of fake and 56k hours of real speech; 16 kHz input of any length; outputs fake and real scores | CC BY-NC-SA 4.0 | Third detector, and the one to fine-tune if you fine-tune anything | NDA means trained **without** data augmentation. The same family has a default variant trained with augmentation, which may cope better with compression; test both. Its card asks for Python 3.9 and fairseq 0.12.2, so run it in an isolated WSL environment as its own worker. Non-commercial and share-alike terms apply to derivatives |
| **AASIST and AASIST-L** (clovaai) | Raw-waveform graph-attention model trained on ASVspoof 2019 LA. README: EER 0.83% (AASIST), 0.99% (AASIST-L, 85,306 parameters) | MIT | Light model that runs on CPU; also the clearest demonstration of the gap between a clean lab number and real audio | Trained on one clean dataset. Expect and measure weaker results on compressed or other-language audio |
| **ASVspoof 5 baselines** (RawNet2 and AASIST code, evaluation package) | Challenge baselines and scoring tools | None shown on GitHub | Read it to learn how the challenge scores systems: minDCF is primary; CLLR, EER and actDCF are secondary | Treat the code as not ours to copy. Implement EER and your other metrics from their definitions. The data comes under the challenge's own terms |

All numbers above are as printed on the pages. I have not reproduced any of them.

### Decisions

1. **An ensemble, never a single model.** Run at least two detectors from different families. Disagreement is a result: it is
   flagged and the audio verdict becomes `INCONCLUSIVE`.
2. **Raw scores are not comparable across models.** Calibrate each one on held-out data before combining.
3. **Compression is the main test.** Every claim about audio is reported per codec condition.
4. **Isolate environments.** ONNX models run in the main environment through `onnxruntime`. The fairseq-based model runs in a
   separate WSL worker behind the same `Detector` contract. Never install fairseq in the main environment.
5. **Event rule.** The pretrained detectors are integrations. What you build in the window is the gate, the degradation
   pipeline, calibration, fusion, the evaluation and any fine-tuning. Ask the organisers once, in writing, and say plainly in the
   demo which parts are pretrained.
6. **Voice data ethics.** Make fake test audio only from voices of teammates who consent, or from generic synthetic voices. Never
   clone a real public figure for a demo or a test set. Label every self-generated file as such in its manifest.

### A0: Day-0 (about 1 h, lane 1 with lane 3)
1. Check ffmpeg: `ffmpeg -encoders` must list an Opus encoder. If an AMR or other encoder is missing from your build, skip that
   condition and record the gap.
2. Take **one real forwarded voice note** and run `ffprobe` on it. Write down its codec, sample rate, channels and bitrate in
   `docs/real_voice_note.md`. This tells you what degradation looks like in practice; I expect voice notes are often Opus, but
   confirm it on your own sample.
3. Datasets: ASVspoof 2019 LA (Edinburgh DataShare), an in-the-wild set, a sample of ASVspoof 5, and bona fide speech in the
   Indian languages you will demo. For each, check that it covers your languages and what its terms allow. Request access now.
4. Download the Spectra-AASIST and W2V2-AASIST ONNX files. Record their sha256 and licence in `docs/licences.md`.

### A1: Audio input and quality gate (about 2 h)
Files: `forensics/audio/io.py`, `forensics/audio/quality.py`, `tests/unit/test_audio_quality.py`.
1. Decode with ffmpeg to 16 kHz mono float32. Keep the **original** sample rate, codec, channel count and bitrate from `ffprobe`.
2. Measure: duration; speech fraction from a voice-activity detector (write a simple energy-based one, or use an open one whose
   licence allows it); clipping fraction; level; and estimated **bandwidth** (the frequency above which energy stays below a
   floor), since low bandwidth means telephone-band audio.
3. Gate rules read thresholds from config. Return reasons such as `too_short`, `too_little_speech`, `clipped`, `narrowband`,
   `heavily_compressed`.
4. The thresholds are not guessed. They come from A5, which finds the conditions where error exceeds the target.
5. Tests: silent file, music only, clipped file, one-second clip, a telephone-band file made with ffmpeg inside the test.

Done when: each test case abstains with the right reason and a clean speech clip passes.

### A2: Detector wrappers (about 4 h)
Files: `forensics/audio/detectors/spectra_aasist.py`, `w2v2_aasist.py`, `base.py`.
1. Load each ONNX file with `onnxruntime`. **Read the input name, shape and type from the session** with `get_inputs()`, and
   take sample rate and crop length from each model card. Do not hard-code them.
2. If a model expects a fixed number of samples, split longer audio into windows with overlap, pad shorter windows by repeating,
   and score each window. Report the mean logit as the clip score and keep the per-window scores for the UI. Compare mean and
   maximum on the evaluation set before choosing.
3. **Polarity test.** Each model's output can mean bonafide or spoof. Verify with one known bonafide and one known spoof file
   per model and store the direction in config. A wrong direction silently inverts every verdict.
4. Each wrapper returns a `DetectorResult` with its model name, sha256 and the window timeline. Execution provider (CPU or GPU) is
   read from config.
5. Benchmark seconds of audio processed per second of compute for each model, on this machine, into `docs/audio_bench.md`.

Done when: polarity and determinism tests pass (same file, same score) and the benchmark table exists.

### A3: Evaluation harness (about 3 h, lane 1)
Files: `scripts/eval_audio.py`, `forensics/audio/metrics.py`.
1. Write the metrics yourself: equal error rate, ROC AUC, the **false-fake rate on bonafide audio at the deployed threshold**,
   and accuracy against the fraction answered when abstention is allowed. Report minDCF only after reading how the challenge
   defines it.
2. Dataset loaders read each dataset's protocol file; never infer labels from folder names without checking.
3. Split **by speaker**. No speaker appears in both calibration and test data.
4. Run every detector on: the in-domain set; a different dataset (cross-dataset); and each condition from A4.
5. Output a markdown table and a JSON file to `docs/`, including the Indian-language slice and the bonafide-only slice.

Done when: `docs/audio_baselines.md` is generated by one command and includes an in-domain row and a cross-dataset row for each
detector.

### A4: Degradation pipeline (about 2 h, lane 1)
Files: `scripts/make_degraded_audio.py`, `config/audio_conditions.yaml`.
1. Conditions come from config and from what A0 found: the original; Opus at several bitrates; AMR narrowband if available; MP3;
   AAC; band-limited to 8 kHz; added noise at several signal-to-noise ratios; and **two-hop chains** (encoded, then encoded again),
   because forwarded audio is often re-encoded.
2. Use ffmpeg through a subprocess with an argument list, never a shell string built from filenames.
3. Write a manifest CSV: source file, condition, output hash. Keep the clean originals so the same speakers appear in every
   condition.

Done when: one command turns a folder of clean clips into every condition and writes the manifest.

### A5: Calibration, fusion and the gate (about 3 h)
Files: `forensics/audio/calibrate.py`, `fuse.py`, `scripts/calibrate_audio.py`, `config/thresholds.yaml`.
1. On the calibration speakers, fit a mapping from each detector's logit to a probability (Platt or isotonic), using clean plus
   degraded audio in proportions set in config.
2. Fit the ensemble as a logistic regression on the calibrated logits. Keep it small and look at its weights.
3. Choose the abstain band so that the false-fake rate on bonafide audio, **in each condition**, stays under the target set in
   config. Where no usable band exists, that condition becomes a gate failure.
4. Disagreement rule: if two detectors fall on opposite sides by a margin from config, return `CONFLICT` and abstain.
5. Save thresholds with the model hashes, the manifest hash, the date and the code version.

Done when: `scripts/calibrate_audio.py` regenerates the thresholds, and the report shows the false-fake rate by condition.

**Decision gate.** If the false-fake rate on compressed Indian-language bonafide audio cannot be brought under the target at any
useful coverage, ship audio as "abstains on voice notes below quality X", show the condition matrix in the demo, and do not
claim detection there.

### A6: AntiDeepfake worker (Tier 2, about 4 h)
1. In WSL create a Python 3.9 environment as the model card describes, install its pinned dependencies, and run its inference
   example on one file.
2. Wrap it in a small HTTP service that accepts audio and returns scores. The adapter has a timeout and raises `Unreachable`
   when the worker is down.
3. Test the NDA variant and the default (augmented) variant on the A4 conditions and keep whichever calibrates better.
4. Add it to the ensemble only if it improves the by-condition table.

### A7: Fine-tuning (Tier 3, about 8 h, only after the A5 gate)
Do this only if A3 and A4 show a real weakness on your target conditions and you have a GPU.
1. Start by training only a small head on frozen embeddings, with degraded audio. Full fine-tuning is a later step; the model
   card's own training used a very small learning rate, so keep yours small.
2. Train on speaker-disjoint data and test on a different dataset.
3. Remember that fine-tuned weights of the AntiDeepfake model inherit its non-commercial, share-alike terms.
4. Use the NII project's own training repository as a reading reference. I have not read it.

### A8: Reviewer spectrogram view (Tier 2, about 3 h)
Render the suspect clip beside a reference clip on **identical axes**: the same frequency range, decibel floor, colour scale and
time scale. Add the per-window score timeline under it. Computing mel spectrograms with plain NumPy keeps the dependencies small.

### A9: Service and drills (about 2 h)
Files: `forensics/service.py`, `api/routes/forensics.py`, `tests/drills/test_audio.py`.
1. `POST /forensics/audio` accepts a file with size and type limits, queues a job and returns a `DetectorResult` with the gate
   verdict, per-detector calibrated scores and the window timeline. Store model hashes in the ledger entry.
2. Drills: silent file; music only; two speakers; three-second clip; very long file; wrong sample rate; mono and stereo;
   corrupt container; a video file with no audio track; a file over the size limit.

Done when: every drill returns a clean abstention or error, and a good clip returns calibrated scores from both ONNX detectors.

### Audio evaluation protocol
Report each of these separately, per detector and for the ensemble:
1. In-domain (the set the model family was trained on).
2. Cross-dataset.
3. Every A4 condition, one and two hops.
4. Speaker-disjoint calibration and test.
5. Indian-language slice, if you have data; otherwise state that no such result exists.
6. False-fake rate on bonafide audio at the deployed threshold, in every condition.
7. Accuracy against coverage with abstention.
Quote EER only next to the false-fake rate, because EER hides the operating point.

## 5B. Image track (phases I0 to I8)

Two different questions get two different tools and two separate results. **Was this image generated by AI?** is answered by
classifiers. **Was a real photo edited or spliced?** is answered by a localisation model that draws a map. A single image can be
neither, either, or both, and neither answer says whether the image is *misleading*; that depends on the claim attached to it
(claims track).

### The image resources

| Resource | What it is | Licence shown | Role | Cautions |
|---|---|---|---|---|
| **Organika/sdxl-detector** | Image classifier fine-tuned from the umm-maybe detector on Wikimedia and SDXL image pairs; has an ONNX file | cc-by-nc-3.0 | General AI-image detector; first one to wire up | Its card says it may do worse on generators other than SDXL and on older ones such as VQGAN+CLIP. Validation figures are on its own data |
| **SadraCoding/SDXL-Deepfake-Detector** | Swin backbone plus a learned branch on the image's Fourier spectrum, fine-tuned from Organika, trained on the 140k Real and Fake Faces set. Labels: 0 = `artificial`, 1 = `human`. Input resized to 224 × 224 | **cc-by-nc-3.0 on its Hugging Face page** (your note said MIT; check the page and its LICENSE file yourself) | Face-specific second opinion | Trained on faces, so it is not valid on scenes or objects. The card calls it an experimental research model, not proof, and says softmax scores are not calibrated. Its optional early-exit path changes the computation; fix it in config |
| **umm-maybe/AI-image-detector** | Older ViT detector for artistic images, from 2022 | Metadata says cc-by-4.0; the card text says CC BY-ND 4.0 | Only for old generators; optional | The card itself says to use Organika's detector unless you need older generators, and that webcams and screenshots may throw it off |
| **AI-Image-Detector (Space, MobileNetV2)** | Small Keras model in a Hugging Face Space, trained on CIFAKE and Tiny GenImage per your notes | MIT in the Space metadata | Fast fallback and a latency reference | I did not read its page. Very small training images usually do not generalise to real social-media photos; measure it before trusting it |
| **TruFor** (GRIP-UNINA) | Forgery detection and localisation: a pixel-level map of suspicious regions, a 0 to 1 integrity score and a reliability map. Docker setup for inference; training code is available | No standard licence; the README limits use to informational and nonprofit purposes under its own LICENSE terms | The edit and splice detector, and the strongest visual in the demo | Read its LICENSE before you use it. Do not copy weights or code into our repository; run it as a separate container. A community retrain exists on Hugging Face; prefer the official release |
| **UniGenDet** (weights and code) | A 7B-parameter model that detects AI images and writes a sentence explaining its verdict | Weights Apache-2.0 on Hugging Face; code MIT on GitHub; it builds on BAGEL-7B-MoT, whose own licence I did not check | Tier 3 explainer | Needs a large GPU, Python 3.10, flash-attn and the base weights. The explanation is generated text, so show it labelled as a model's description, never as evidence |

All validation figures on these pages are the authors' own. I have not reproduced any of them.

### Decisions

1. **Two questions, two outputs.** Classifier scores feed `SYNTHETIC_SUSPECTED`. TruFor feeds `EDITED_REGION` with the map
   attached. They never merge into one score.
2. **Route by content.** Run the face-trained detector only when a face is present; otherwise its output is invalid and it
   abstains.
3. **Every detector sees recompressed images.** Social photos are resized and re-encoded. The 224 × 224 resize inside these
   models discards fine detail, so calibrate on degraded copies.
4. **Training-data overlap is a trap.** The face detector was trained on the 140k Real and Fake Faces set, so that set cannot be
   its held-out test. Check the training data of every model before choosing its test set.
5. **Isolate TruFor.** Run it in its own container behind the `Detector` contract; its dependencies stay out of the main
   environment.
6. **A missing EXIF block proves nothing.** Messaging apps strip it. Absence is not evidence either way.
7. **Event rule.** These are pretrained models and a pretrained forensic framework. What we build is the gate, routing,
   calibration, fusion, the evaluation and the heatmap view. State this in the demo.

### I0: Day-0 (about 1 h)
1. Download Organika (the ONNX file is listed in its repository), SDXL-Deepfake-Detector, and the Docker files for TruFor.
2. For each, open the Hugging Face or GitHub licence page and write the exact terms in `docs/licences.md`. Where the metadata
   and the card text disagree, record both.
3. Run TruFor's Docker inference on one image from its own instructions, and note how long it takes on your machine and whether it
   used a GPU.

### I1: Image input and gate (about 2 h)
Files: `forensics/image/io.py`, `forensics/image/quality.py`, `tests/unit/test_image_quality.py`.
1. Open with Pillow, apply the EXIF orientation, convert to RGB, reject animated or zero-size images with a reason.
2. Measure: width and height; JPEG quantisation tables (Pillow exposes them for JPEG files) as a rough quality estimate; whether
   EXIF exists; file size per pixel; and the number of faces from the face model already used in the pulse track.
3. Gate reasons: `too_small`, `heavily_recompressed`, `no_face` (for the face detector only), `unsupported_format`.
4. Thresholds come from I5, not guesses.

Done when: a tiny image, a heavily compressed image and a face-less image each return the correct reason in tests.

### I2: Classifier wrappers (about 3 h)
Files: `forensics/image/detectors/organika.py`, `sdxl_face.py`, `base.py`.
1. Load with `AutoImageProcessor` and `AutoModelForImageClassification` from the model identifier in config. Read
   `model.config.id2label` and map labels to "synthetic" and "real" **from that file**, not by assuming an order. For the face
   model the card says 0 is `artificial` and 1 is `human`; confirm it against the config.
2. **Polarity test:** one known real photo and one known synthetic image per model; store the direction in config.
3. Turn the early-exit path off for the face model during calibration, or keep it on in both calibration and use, never differently.
4. Batch inputs, set the device from config, and pin model revisions by hash.
5. Benchmark seconds per image on this machine into `docs/image_bench.md`. Add the small Keras model only for comparison.

Done when: polarity and determinism tests pass and both wrappers return `DetectorResult`.

### I3: TruFor service (about 3 h)
Files: `forensics/image/trufor_client.py`, `scripts/run_trufor.sh`, `docker/trufor/`.
1. Build or pull the container following its instructions. Wrap it in a small HTTP service that takes an image and returns the
   localisation map, the integrity score and the reliability map.
2. The client has a timeout and raises `Unreachable` when the container is down.
3. Produce an overlay: the map as a transparent colour layer on the image, with the reliability map shown as a second layer or a
   mask where predictions are unreliable.
4. Rule: where reliability is low, the result for that region is "unreliable", not "clean".
5. Do not copy weights or code into the repository; the container fetches what it needs.

Done when: an image returns a map, a score and a reliability layer, and the overlay renders in the UI.

### I4: Image evaluation data (about 3 h, lane 1)
Files: `scripts/build_image_eval.py`, `config/image_conditions.yaml`.
1. **Real images:** from datasets whose terms allow it, plus photos from consenting teammates.
2. **Synthetic images:** generate with open image generators during the window, label them as self-generated in the manifest, and
   cover more than one generator so you can hold one out. Do not generate images of real public figures.
3. **Edited images:** use the CocoGlide set the TruFor authors released (check its terms), plus splices you make yourselves from
   consenting teammates' photos, with the region mask saved.
4. **Degradation ladder:** JPEG quality levels, resize factors, screenshot-style re-encoding, a two-hop chain.
5. Write a manifest with source, label, generator, mask path, condition and hash. Split so that no generator, and no source
   photo, is in both calibration and test.

Done when: one command builds every condition from the clean images and writes the manifest.

### I5: Calibration, routing and fusion (about 3 h)
Files: `forensics/image/calibrate.py`, `fuse.py`, `scripts/calibrate_image.py`.
1. Calibrate each classifier on held-out data with degraded copies. Report false-synthetic rate on real images per condition.
2. Combine the classifiers with a small logistic regression on calibrated logits. A face detector contributes only on images
   with a face.
3. Disagreement rule: classifiers falling on opposite sides by a config margin give `CONFLICT` and abstain.
4. TruFor: map its integrity score to a probability on the edited-image test set, and report localisation quality by overlap with
   the saved masks. Keep it separate from the classifier ensemble.
5. Choose the gate from the conditions where the false-synthetic rate exceeds the target, and store thresholds with model hashes.

**Decision gate.** If no usable operating point exists for recompressed images, say so: the image classifier abstains on heavily
recompressed photos, and the demo shows the by-condition matrix.

### I6: Service and drills (about 2 h)
`POST /forensics/image` returns the gate result, per-detector calibrated scores, flags (`SYNTHETIC_SUSPECTED`, `EDITED_REGION`,
`CONFLICT`, `GATE_FAILED`) and a link to the overlay. Drills: tiny image; huge image; CMYK or palette image; transparent PNG;
animated GIF; image with no face sent to the face model; a screenshot; a photo of a screen; a corrupt file; TruFor container down.

### I7: Heatmap view (about 2 h)
Show the original, the overlay, a reliability toggle and the integrity score with its wording ("possible local edit", not
"fake"). Show every detector's calibrated score and the gate verdict, and mark any unavailable one.

### I8: UniGenDet explainer (Tier 3, about 6 h)
Only with a large GPU and spare time. Follow its repository's installation, run its detection mode on a few images, and display
the sentence it writes under the label "model description, not evidence". Never feed that text into the verdict.

### Image evaluation protocol
Per detector and for the ensemble:
1. In-domain (the set closest to each model's training data), cross-generator, cross-dataset.
2. Every condition in the degradation ladder.
3. False-synthetic rate on real images at the deployed threshold.
4. Face and non-face slices.
5. TruFor: integrity-score agreement with edited or untouched labels, and overlap between its map and the saved masks.
6. Accuracy against coverage with abstention.

## 6. How the forensic result enters fusion

| Situation | What fusion may do |
|---|---|
| Pulse gate failed | Ignore the pulse; say why |
| Pulse gate passed, score low | Mild support for authentic. Never `AUTHENTIC_IN_CONTEXT` on this alone |
| Pulse gate passed, score high | Contributes toward `SYNTHETIC`, with image and audio evidence if present |
| Pulse says real, another detector says synthetic | Report the conflict; do not average it away |
| Image classifiers agree above the band | `SYNTHETIC_SUSPECTED` flag; contributes toward `SYNTHETIC` |
| Image classifiers disagree, or the gate failed | `CONFLICT` or `GATE_FAILED`; abstain and show each score |
| TruFor integrity score low with good reliability | `EDITED_REGION` flag with the map attached; wording stays "possible local edit" |
| TruFor reliability low | Region reported as unreliable, never as clean |
| An image is synthetic but the claim does not depend on it (an illustration, say) | Report the flag; the claims track decides whether the post is misleading |
| Audio gate failed | Ignore the audio score; say why |
| Audio detectors agree and calibrate above the band | Contributes toward `SYNTHETIC` for that audio track |
| Audio detectors disagree | `CONFLICT`, abstain, show each detector's calibrated score |
| Audio is real but the video shows a swapped face (or the reverse) | Report each track separately; a clip can be partly synthetic |
| Provenance (C2PA or earliest copy) is intact | Counts above any detector |
| Reverse search finds an earlier appearance that contradicts the claimed date or place | `OUT_OF_CONTEXT`; outranks every score in this file |
| Reverse search finds nothing | No provenance verdict; never `AUTHENTIC_IN_CONTEXT` on this alone |

## 7. Pitfalls to avoid

- Using the nominal frame rate instead of the measured one.
- Splitting windows of one video across train and test, which inflates every metric.
- Averaging gate-failed windows into a score.
- Choosing thresholds by eye. They come from `calibrate_quality_gate.py` and are stored with their provenance.
- Reporting the best single number. Report the slice table.
- Copying code or weights from the DeepFakesON-Phys repository, or distributing Toolbox code without its licence.
- Assuming a measured pulse means a real video.
- Quoting a model card's EER as your accuracy. Cards are self-reported, may overlap their training data, and the W2V2-AASIST
  card itself shows results near chance on some sets.
- A wrong score direction (bonafide versus spoof) or a wrong sample rate. Test both.
- Averaging uncalibrated scores from different models.
- Calibrating and testing on the same speakers.
- Testing only on clean audio when the real input is a forwarded, re-encoded voice note.
- Installing fairseq in the main environment.
- Treating CC BY-NC-SA weights as free for any use, or copying code from a repository with no licence.
- Making fake audio from a real person's voice without their consent.
- Testing an image model on the dataset it was trained on, or running a face model on a scene.
- Reading a softmax score as a probability, or merging "AI-generated" with "edited" with "misleading".
- Treating missing EXIF as evidence, or a low TruFor reliability as a clean result.
- Presenting a model's generated explanation as proof.
- Assuming a licence from a summary: the SDXL face detector's page says cc-by-nc-3.0 while a note said MIT; read the page.

## 8. Claim-sheet rows this track must fill

| Claim | Reproduce | Not claimed |
|---|---|---|
| Pulse estimate error against ground truth | `scripts/eval_hr.py` | Accuracy on moving or compressed video |
| Gate abstains on degraded video | `scripts/calibrate_quality_gate.py` | Behaviour on codecs not in the sweep |
| Video detector AUC, grouped and cross-dataset | `scripts/eval_forensics.py` | Detection of pulse-copying fakes |
| False-fake rate on real, recompressed video | `scripts/eval_forensics.py` | Any population not in the test sets |
| Image classifiers, cross-generator and by degradation condition | `scripts/eval_image.py` | Generators and conditions not in the test sets |
| False-synthetic rate on real images by condition | `scripts/eval_image.py` | Photos unlike the evaluation set |
| TruFor map overlap with saved masks, and its integrity score on edited versus untouched images | `scripts/eval_image.py` | Edits made by methods not represented |
| Image ensemble calibration and abstention | `scripts/calibrate_image.py` | Any claim that an image is misleading |
| Audio detectors in-domain and cross-dataset, per detector | `scripts/eval_audio.py` | Accuracy on languages or codecs not tested |
| Audio false-fake rate on bonafide audio by codec condition | `scripts/eval_audio.py` | Conditions not in `audio_conditions.yaml` |
| Audio ensemble calibration and abstention | `scripts/calibrate_audio.py` | Voices cloned by systems not in the test data |
| Audio detector disagreement is flagged, not averaged | `pytest tests/unit/test_audio_fuse.py` | That agreement means the audio is genuine |
