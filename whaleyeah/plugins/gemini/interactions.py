"""Rebuild stateless conversation steps from Interactions SSE events."""

import base64
from copy import deepcopy
import json
from typing import Any


def _payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        # The SDK retains unrecognized union variants in a raw wrapper.
        if value.get("is_unknown") is True and "raw" in value:
            return _payload(value["raw"])
        return {key: _payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_payload(item) for item in value]
    return value


def _merge_delta(target: dict, delta: dict) -> None:
    for key, value in delta.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_delta(target[key], value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key].extend(deepcopy(value))
        elif key in {"code", "result"} and isinstance(value, str):
            target[key] = target.get(key, "") + value
        else:
            target[key] = deepcopy(value)


def _append_content(contents: list[dict], content: dict) -> None:
    if content["type"] == "text" and contents and contents[-1]["type"] == "text":
        contents[-1]["text"] += content["text"]
        if content.get("annotations"):
            contents[-1].setdefault("annotations", []).extend(content["annotations"])
    else:
        contents.append(deepcopy(content))


class InteractionStream:
    """Keep API history separate from Telegram's rendered answer."""

    def __init__(self) -> None:
        self._steps: dict[int, dict] = {}
        self._open_steps: set[int] = set()
        self._arguments: dict[int, str] = {}
        self.completed = False

    @property
    def steps(self) -> list[dict]:
        return deepcopy([self._steps[index] for index in sorted(self._steps)])

    def consume(self, event: Any) -> None:
        event = _payload(event)
        event_type = event["event_type"]
        if event_type == "error":
            error = event.get("error", {})
            raise RuntimeError(error.get("message", "Gemini interaction failed"))
        if event_type == "interaction.completed":
            status = event["interaction"]["status"]
            if status != "completed":
                raise RuntimeError(f"Gemini interaction ended with status: {status}")
            if self._open_steps:
                raise RuntimeError("Gemini interaction ended with unfinished steps")
            self.completed = True
        elif event_type == "step.start":
            index = event["index"]
            if index in self._steps:
                raise RuntimeError(f"Duplicate Gemini step: {index}")
            self._steps[index] = deepcopy(event["step"])
            self._open_steps.add(index)
        elif event_type in {"step.delta", "step.stop"}:
            index = event["index"]
            if index not in self._open_steps:
                raise RuntimeError(f"Gemini event has no open step: {index}")
            step = self._steps[index]
            if event_type == "step.stop":
                if index in self._arguments:
                    step["arguments"] = json.loads(self._arguments.pop(index))
                self._open_steps.remove(index)
                return

            delta = deepcopy(event["delta"])
            delta_type = delta.pop("type")
            if delta_type == "thought_signature":
                step.update(delta)
            elif delta_type == "thought_summary":
                if delta.get("content"):
                    _append_content(step.setdefault("summary", []), delta["content"])
            elif delta_type == "arguments_delta":
                self._arguments[index] = self._arguments.get(index, "") + delta["arguments"]
            elif delta_type == "text_annotation_delta":
                texts = [item for item in step.get("content", []) if item["type"] == "text"]
                if not texts:
                    raise RuntimeError("Gemini citation has no text content")
                texts[-1].setdefault("annotations", []).extend(delta.get("annotations", []))
            elif step["type"] == "model_output" and delta_type in {
                "text", "image", "audio", "video", "document",
            }:
                _append_content(step.setdefault("content", []), {"type": delta_type, **delta})
            elif delta_type == step["type"]:
                _merge_delta(step, delta)
            else:
                raise RuntimeError(f"Unsupported Gemini delta: {delta_type}")

    def finish(self) -> list[dict]:
        if not self.completed:
            raise RuntimeError("Gemini stream ended before interaction.completed")
        return self.steps

    @property
    def text(self) -> str:
        parts = []
        for index in sorted(self._steps):
            step = self._steps[index]
            match step["type"]:
                case "model_output":
                    parts.extend(item["text"] for item in step.get("content", []) if item["type"] == "text")
                case "code_execution_call":
                    code = step.get("arguments", {}).get("code", "")
                    if code:
                        parts.append(f"\n🛠️代码执行：\n```\n{code}\n```\n")
                case "code_execution_result":
                    if step.get("result"):
                        parts.append(f"\n💻执行结果：\n{step['result']}\n")
        return "".join(parts)

    @property
    def image_bytes(self) -> bytes | None:
        # Preserve the existing plugin's last-image delivery behavior.
        images = [
            item
            for index in sorted(self._steps)
            for item in self._steps[index].get("content", [])
            if item["type"] == "image" and item.get("data")
        ]
        return base64.b64decode(images[-1]["data"]) if images else None
