"""로컬 STT 모델로 녹음 180개를 전사하고 처리 시간을 잰다.

사용법: python run_asr.py <model> [--hint none|location|global] [--out results_dir] [--repo 경로]
결과: <out>/<model>[+<hint>].jsonl  (id, hyp, proc_sec, duration)

- 모델 로딩 시간은 빼고, 첫 파일로 1회 워밍업한 뒤 잰다.
- proc_sec는 전처리(특징 추출)부터 텍스트 디코딩까지 걸린 시간이다.
- Whisper는 macOS에서 mlx-whisper, Linux(CUDA)에서 faster-whisper(CTranslate2)로 돌린다.
  둘 다 fp16, greedy(beam 1) 디코딩이다.
"""
import argparse
import csv
import json
import platform
import sys
import time
from pathlib import Path

import soundfile as sf

from postcorrect import live_post

ROOT = Path(__file__).resolve().parent
WAV_DIR = ROOT / ".cache" / "wav16k"


def torch_device():
    import torch
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def hint_text(terms, style):
    if style == "whisper":
        return "경복궁 해설입니다. " + ", ".join(terms) + "."
    return "경복궁 해설 용어: " + ", ".join(terms)


class MlxWhisper:
    def __init__(self, repo):
        import mlx_whisper
        self.mlx_whisper, self.repo = mlx_whisper, repo
        self.device, self.dtype = "mlx(gpu)", "float16"

    def __call__(self, audio, terms):
        prompt = None
        if terms:
            # Whisper 프롬프트는 223토큰까지만 쓰이므로 앞(빈도 높은 용어)부터 잘라 넣는다.
            from mlx_whisper.tokenizer import get_tokenizer
            tok = get_tokenizer(multilingual=True, language="ko", task="transcribe")
            while terms and len(tok.encode(" " + hint_text(terms, "whisper"))) > 223:
                terms = terms[:-1]
            prompt = hint_text(terms, "whisper")
        out = self.mlx_whisper.transcribe(audio, path_or_hf_repo=self.repo, language="ko",
                                          initial_prompt=prompt, verbose=None)
        return out["text"].strip()


class FasterWhisper:
    def __init__(self, repo):
        from faster_whisper import WhisperModel
        from faster_whisper.tokenizer import Tokenizer
        self.model = WhisperModel(repo, device="cuda", compute_type="float16")
        self.tok = Tokenizer(self.model.hf_tokenizer, True, task="transcribe", language="ko")
        self.device, self.dtype = "cuda", "float16"

    def __call__(self, audio, terms):
        prompt = None
        if terms:
            while terms and len(self.tok.encode(" " + hint_text(terms, "whisper"))) > 223:
                terms = terms[:-1]
            prompt = hint_text(terms, "whisper")
        segments, _ = self.model.transcribe(audio, language="ko", initial_prompt=prompt, beam_size=1,
                                            condition_on_previous_text=True, vad_filter=False)
        return "".join(s.text for s in segments).strip()


class QwenAsr:
    def __init__(self, repo):
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        self.torch = torch
        self.device, self.dtype = torch_device(), "float16"
        self.processor = AutoProcessor.from_pretrained(repo)
        self.model = AutoModelForMultimodalLM.from_pretrained(repo, dtype=torch.float16).to(self.device)

    guard = False  # True면 사전 목록을 그대로 출력한 경우 사전 없이 다시 받아쓴다
    retries = 0

    def __call__(self, audio, terms):
        hyp = self._transcribe(audio, terms)
        if self.guard and terms and "해설 용어" in hyp:
            QwenAsr.retries += 1
            hyp = self._transcribe(audio, None)
        return hyp

    def _transcribe(self, audio, terms):
        kw = {"prompt": hint_text(terms, "qwen")} if terms else {}
        inputs = self.processor.apply_transcription_request(audio=audio, language="Korean", **kw)
        inputs = inputs.to(self.model.device, self.model.dtype)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=256, do_sample=False)
        gen = out[:, inputs["input_ids"].shape[1]:]
        return self.processor.decode(gen, return_format="transcription_only")[0].strip()


class CohereAsr:
    def __init__(self, repo):
        import torch
        from transformers import AutoProcessor, CohereAsrForConditionalGeneration
        self.torch = torch
        self.device, self.dtype = torch_device(), "float16"
        self.processor = AutoProcessor.from_pretrained(repo)
        self.model = CohereAsrForConditionalGeneration.from_pretrained(repo, dtype=torch.float16).to(self.device)

    def __call__(self, audio, terms):
        if terms:
            raise SystemExit("Cohere Transcribe는 용어 힌트(프롬프트) 입력을 지원하지 않습니다.")
        inputs = self.processor(audio, sampling_rate=16000, return_tensors="pt", language="ko")
        inputs.to(self.model.device, dtype=self.model.dtype)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=256)
        return self.processor.decode(out, skip_special_tokens=True)[0].strip()


WHISPER = {
    "darwin": (MlxWhisper, "mlx-community/whisper-large-v3-mlx", "mlx-community/whisper-large-v3-turbo"),
    "linux": (FasterWhisper, "Systran/faster-whisper-large-v3", "mobiuslabsgmbh/faster-whisper-large-v3-turbo"),
}[sys.platform]
MODELS = {
    "whisper-large-v3": (WHISPER[0], WHISPER[1]),
    "whisper-large-v3-turbo": (WHISPER[0], WHISPER[2]),
    "qwen3-asr-1.7b": (QwenAsr, "Qwen/Qwen3-ASR-1.7B-hf"),
    "qwen3-asr-0.6b": (QwenAsr, "Qwen/Qwen3-ASR-0.6B-hf"),
    "cohere-transcribe": (CohereAsr, "CohereLabs/cohere-transcribe-03-2026"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", choices=MODELS)
    ap.add_argument("--hint", choices=["none", "location", "global", "loo"], default="none",
                    help="loo: 평가하는 그 문장을 뺀 위치별 사전 (정보 누출 없음)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만 (동작 확인용)")
    ap.add_argument("--out", default="results", help="결과 폴더 (장비별로 나눌 때)")
    ap.add_argument("--repo", help="모델 저장소 대신 쓸 로컬 경로")
    ap.add_argument("--post", action="store_true", help="받아쓰기 직후 후처리 사전(위치별·자모·0.85) 적용")
    ap.add_argument("--guard", action="store_true", help="Qwen: 사전 목록 출력(붕괴) 시 사전 없이 재시도")
    args = ap.parse_args()
    res_dir = ROOT / args.out

    refs = list(csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8")))
    if args.limit:
        refs = refs[:args.limit]
    loc_hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    all_hints = (ROOT / "data" / "hint_terms.txt").read_text(encoding="utf-8").splitlines()
    loo_hints = json.loads((ROOT / "data" / "hints_loo.json").read_text(encoding="utf-8"))

    def terms_for(r):
        return {"none": None, "location": loc_hints[r["location"]], "global": all_hints,
                "loo": loo_hints[r["id"]]}[args.hint]

    name = (args.model + ("" if args.hint == "none" else f"+{args.hint}") + ("-guard" if args.guard else "")
            + ("+post" if args.post else ""))
    res_dir.mkdir(exist_ok=True)
    out_path = res_dir / f"{name}.jsonl"
    done = {json.loads(l)["id"] for l in out_path.open(encoding="utf-8")} if out_path.exists() else set()

    cls, repo = MODELS[args.model]
    repo = args.repo or repo
    t0 = time.perf_counter()
    asr = cls(repo)
    asr.guard = args.guard
    load_sec = time.perf_counter() - t0
    load = lambda r: sf.read(WAV_DIR / f"{r['id']}.wav", dtype="float32")[0]
    asr(load(refs[0]), terms_for(refs[0]))  # 워밍업

    meta = {"model": args.model, "repo": repo, "hint": args.hint, "device": asr.device,
            "dtype": asr.dtype, "load_sec": round(load_sec, 1), "machine": platform.platform()}
    (res_dir / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    with out_path.open("a", encoding="utf-8") as fp:
        for i, r in enumerate(refs, 1):
            if r["id"] in done:
                continue
            audio = load(r)
            t = time.perf_counter()
            hyp = raw = asr(audio, terms_for(r))
            if args.post:
                hyp, _ = live_post(loc_hints[r["location"]], raw)
            dt = time.perf_counter() - t
            fp.write(json.dumps({"id": r["id"], "hyp": hyp, "proc_sec": round(dt, 4), "duration": float(r["duration"]),
                                 **({"hyp_raw": raw} if args.post else {})}, ensure_ascii=False) + "\n")
            fp.flush()
            if i % 30 == 0 or i == len(refs):
                print(f"[{name}] {i}/{len(refs)}", flush=True)
    if args.guard:
        print(f"[{name}] 사전 목록 출력으로 재시도 {QwenAsr.retries}건", flush=True)


if __name__ == "__main__":
    main()
