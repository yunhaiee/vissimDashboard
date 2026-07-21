"""JSON-lines worker that keeps the local Qwen model loaded."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import threading

import torch
from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig, TextIteratorStreamer


SYSTEM_PROMPT = """You are a VISSIM Web Dashboard assistant.
Always answer in the language requested in the prompt (Korean, English, or Japanese); if none is specified, match the user's language.
Use only the supplied dashboard context.
If the data is incomplete, missing, or estimated, say so briefly.
Keep answers concise and operational.
Return only the final answer. Do not reveal reasoning, analysis, scratchpad, draft text, or planning notes."""


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def inspect_quantization(model: Any, requested_4bit: bool) -> dict[str, Any]:
    first_parameter = next(model.parameters(), None)
    quantization_config = getattr(getattr(model, "config", None), "quantization_config", None)
    if hasattr(quantization_config, "to_dict"):
        quantization_config = quantization_config.to_dict()

    bitsandbytes_module = None
    bitsandbytes_class = None
    for module in model.modules():
        module_type = type(module)
        module_name = module_type.__module__
        class_name = module_type.__name__
        if "bitsandbytes" in module_name or class_name in {"Linear4bit", "Linear8bitLt"}:
            bitsandbytes_module = module_name
            bitsandbytes_class = class_name
            break

    memory_footprint_bytes = None
    try:
        memory_footprint_bytes = int(model.get_memory_footprint())
    except Exception:
        pass

    return {
        "requested_4bit": requested_4bit,
        "is_loaded_in_4bit": bool(getattr(model, "is_loaded_in_4bit", False)),
        "is_loaded_in_8bit": bool(getattr(model, "is_loaded_in_8bit", False)),
        "bitsandbytes_module": bitsandbytes_module,
        "bitsandbytes_class": bitsandbytes_class,
        "quantization_config": quantization_config,
        "first_parameter_dtype": str(getattr(first_parameter, "dtype", "")) if first_parameter is not None else None,
        "first_parameter_device": str(getattr(first_parameter, "device", "")) if first_parameter is not None else None,
        "memory_footprint_bytes": memory_footprint_bytes,
    }


class QwenWorker:
    def __init__(self, model_dir: Path, load_in_4bit: bool, max_new_tokens: int, temperature: float) -> None:
        self.model_dir = model_dir
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.processor = AutoProcessor.from_pretrained(model_dir)
        kwargs: dict[str, Any] = {
            "device_map": "auto",
            "dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            "low_cpu_mem_usage": True,
        }
        if load_in_4bit:
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        self.model = AutoModelForImageTextToText.from_pretrained(model_dir, **kwargs)
        self.quantization = inspect_quantization(self.model, requested_4bit=load_in_4bit)

    def _prepare_inputs(self, prompt: str, device: str):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            text = self.processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except TypeError:
            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        return self.processor(text=[text], return_tensors="pt").to(device)

    def _generation_kwargs(self, inputs: Any) -> dict[str, Any]:
        return {
            **inputs,
            "max_new_tokens": self.max_new_tokens,
            "do_sample": self.temperature > 0,
            "temperature": self.temperature,
            "eos_token_id": self.processor.tokenizer.eos_token_id,
            "pad_token_id": self.processor.tokenizer.eos_token_id,
        }

    def generate(self, prompt: str) -> str:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        inputs = None
        outputs = None
        generated = None
        try:
            inputs = self._prepare_inputs(prompt, device)
            with torch.inference_mode():
                outputs = self.model.generate(**self._generation_kwargs(inputs))
            generated = outputs[0][inputs["input_ids"].shape[-1] :]
            return self.processor.decode(generated, skip_special_tokens=True).strip()
        finally:
            del generated, outputs, inputs
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def generate_stream(self, prompt: str, request_id: str) -> str:
        """Generate while emitting incremental token events, then return the full
        text. Lets the dashboard render the answer as it is produced."""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        inputs = self._prepare_inputs(prompt, device)
        streamer = TextIteratorStreamer(
            self.processor.tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
            timeout=120.0,
        )
        kwargs = self._generation_kwargs(inputs)
        kwargs["streamer"] = streamer
        thread_error: dict[str, Exception] = {}

        def _run() -> None:
            try:
                with torch.inference_mode():
                    self.model.generate(**kwargs)
            except Exception as exc:  # surfaced after join
                thread_error["error"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        pieces: list[str] = []
        try:
            for piece in streamer:
                if piece:
                    pieces.append(piece)
                    emit({"type": "token", "id": request_id, "text": piece})
        except Exception:
            pass
        finally:
            thread.join()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        if not pieces and thread_error.get("error") is not None:
            raise thread_error["error"]
        return "".join(pieces).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Qwen dashboard worker")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--temperature", type=float, default=0.25)
    args = parser.parse_args()

    try:
        worker = QwenWorker(
            model_dir=Path(args.model_dir).resolve(),
            load_in_4bit=args.load_in_4bit,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
        )
    except Exception as exc:
        emit({"type": "error", "stage": "load", "error": str(exc)})
        raise

    emit(
        {
            "type": "ready",
            "model_dir": str(Path(args.model_dir).resolve()),
            "cuda": torch.cuda.is_available(),
            "quantization": worker.quantization,
        }
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            request_id = str(request.get("id") or "")
            if request.get("type") == "ping":
                emit({"type": "pong", "id": request_id})
                continue
            prompt = str(request.get("prompt") or "").strip()
            if not prompt:
                emit({"type": "response", "id": request_id, "ok": False, "error": "empty prompt"})
                continue
            if request.get("stream"):
                text = worker.generate_stream(prompt, request_id)
            else:
                text = worker.generate(prompt)
            emit({"type": "response", "id": request_id, "ok": True, "text": text})
        except Exception as exc:
            emit({"type": "response", "id": str(locals().get("request_id", "")), "ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
