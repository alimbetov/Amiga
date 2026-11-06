# Language & Voice support — Kokoro-limited policy v1.1

## Contract
UI language (Flutter ARB), content locale (localized templates) and speech locale (qualified voice/model) are THREE separate capabilities. A UI translation does not imply speech support. The engine must reject unsupported combinations: no silent cross-language fallback, no passing Russian or Kazakh text through an English Kokoro voice as a production feature.

Target Kokoro-82M v1.0 language/voice families:
- en-US: af_*, am_* (e.g. af_heart, am_michael)
- en-GB: bf_*, bm_* (e.g. bf_emma)
- es-ES: ef_*, em_*
- fr-FR: ff_*
- hi-IN: hf_*, hm_*
- it-IT: if_*, im_*
- ja-JP: jf_*, jm_*
- pt-BR: pf_*, pm_*
- zh-CN: zf_*, zm_*

Support depends on selected actual ONNX export, phonemizer/G2P, bundled voice assets and license validation. Some distributions omit Japanese or have fewer voices. Do not infer from model name alone that every language is operational.

## Release gates
Phase 1: en-US only, initially validate af_heart and am_michael; en-GB/bf_emma is a candidate only after actual bundle/inference acceptance. Expand other languages independently following pronunciation, native-speaker listening, model bundle checksum, latency, clipping, numbers, dates, medication names and license checks.

Russian ru-RU and Kazakh kk-KZ may have UI/text translations, but Kokoro voice synthesis is NOT advertised as supported. UX: if speech unsupported, offer text/local notification and disable voice picker or clearly label unavailable. Do not automatically change UI locale. Any eventual replacement TTS engine is a separate ADR.

## Backend & Flutter contract
GET /api/v1/voices?language=<BCP47> returns only ACTIVE+QUALIFIED voices, each with engine, modelVersion, locale, voiceCode, bundleChecksum and enabled capabilities. Unsupported language returns empty voice list and explicit speechUnsupported status (not 500). POST /api/v1/audio/render validates locale/voice/model identity against server-side catalog; unsupported combination -> typed VOICE_LOCALE_UNSUPPORTED error; no queue job created. Offline Flutter uses downloaded speech locale capability manifest (with version/hash) and displays textual reminders regardless.

## References
- https://huggingface.co/hexgrad/Kokoro-82M
- https://github.com/hexgrad/kokoro
- https://huggingface.co/litert-community/Kokoro-82M

This is an architecture specification, not evidence that any particular voice has passed acceptance.
