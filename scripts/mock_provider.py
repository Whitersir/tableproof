"""开发检查专用固定响应服务：不是模型，不推理，不接触真实供应商。"""
import argparse
import json

from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()
MATCH = {"registration_keys": ["学号"], "checkin_keys": ["学生编号"], "group_by": "班级"}


def tool_call(name, args, index):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": f"mock-{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


@app.get("/health")
def health():
    return {"kind": "scripted_mock_provider", "not_a_real_model": True}


@app.post("/v1/chat/completions")
async def completion(request: Request):
    body = await request.json()
    if isinstance(body.get("tool_choice"), dict):
        message = tool_call("connection_probe", {"value": "ok"}, "probe")
    else:
        calls = sum(m.get("role") == "assistant" and bool(m.get("tool_calls")) for m in body["messages"])
        if calls == 0:
            message = tool_call("inspect_tables", {}, 1)
        elif calls in (1, 2):
            message = tool_call("reconcile_attendance", MATCH, calls + 1)
        else:
            message = {"role": "assistant", "content": "模拟流程已结束，报告由应用生成。"}
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}, "mock": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    uvicorn.run(app, host="127.0.0.1", port=parser.parse_args().port, log_level="warning")
