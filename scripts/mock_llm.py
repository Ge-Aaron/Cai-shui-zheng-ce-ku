# -*- coding: utf-8 -*-
"""本地 Mock：模拟 OpenAI 兼容的 /v1/chat/completions，便于验证 api_ask 的 AI 调用链路。
真实使用时删除本文件，把 TAXDB_LLM_URL 指向你的网关即可。"""
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def _send(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n).decode("utf-8"))
        user = ""
        for m in req.get("messages", []):
            if m["role"] == "user":
                user = m["content"]
        # 从 user 中抽取用户原场景（【用户场景】\n 之后）
        scene = ""
        m = re.search(r"【用户场景】\s*\n(.+)", user)
        if m:
            scene = m.group(1).strip().split("\n")[0]
        # 依据用户场景构造一个"具体方案"JSON，验证解析与前端渲染（演示数据）
        ans = {
            "summary": f"针对您描述的场景「{scene}」，可直接按下列步骤落地处理（以下为演示数据，配置真实 LLM 密钥后即为实际结论）。",
            "steps": [
                f"先明确事项边界：{scene}。",
                "确认涉及的税种、业务动作与适用主体，定位应适用的现行政策（系统已按相关性匹配到上列政策）。",
                "按政策要求准备资料/表单，通过电子税务局或办税厅办理对应申报、预缴或开票。",
                "办理后留存凭证与计算底稿，便于后续核查与年度汇算。"
            ],
            "calc": "若场景含具体金额，真实 LLM 会代入该数字给出计算式与应纳税额；当前为演示端点，未代入计算。",
            "deadline": "一般为纳税义务发生次月15日内申报缴纳；具体以政策原文与主管税务机关口径为准。",
            "risks": [
                "确保引用的是现行有效政策，已被废止/修订的文件不可再适用。",
                "金额与身份判断易出错，办理前建议核对政策原文与主管税务机关口径。"
            ],
            "basis": [
                "以系统匹配到的现行有效政策为准（见下方「依据政策」与各政策卡片原文）"
            ]
        }
        self._send({"choices": [{"message": {"role": "assistant", "content": json.dumps(ans, ensure_ascii=False)}}]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8799), H).serve_forever()
