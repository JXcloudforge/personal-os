"""OpenAI-compatible deterministic provider used only by isolated R0 browser tests."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path

LOG = Path(os.environ.get('R0_MOCK_LOG', 'r0-mock-provider.log'))
ITEMS = [
    {
        'domain': 'bilingual', 'title': '梳理德语 Agent 规划输入与输出',
        'action': '根据知识卡片整理 Agent 接收的问题、上下文和返回结构。',
        'estimated_minutes': 30, 'why_today': '将知识中的流程转成可验证的开发步骤。',
        'acceptance_criteria': '列出输入字段、输出字段及一个样例，并能用知识卡片核对。',
        'evidence_required': '保存字段对照与样例测试记录。', 'source_task_ids': [], 'sources': [],
        'knowledge_card_ids': [1],
    },
    {
        'domain': 'bilingual', 'title': '为德语 Agent 规划流程补充单元测试',
        'action': '围绕规划输入校验、输出结构和失败边界编写测试。',
        'estimated_minutes': 45, 'why_today': '先固定行为约束，再扩展实现。',
        'acceptance_criteria': '覆盖有效输入、缺失字段和错误响应，测试无需外网。',
        'evidence_required': '记录定向测试命令和通过结果。', 'source_task_ids': [], 'sources': [],
        'knowledge_card_ids': [1],
    },
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as output:
            output.write('POST /v1/chat/completions\n')

    def do_GET(self):
        if self.path != '/health':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status":"ok","provider":"isolated-mock"}')

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        messages = body.get('messages', [])
        has_tool_output = any(message.get('role') == 'tool' for message in messages)
        has_proposal_result = any(
            message.get('role') == 'tool' and 'proposal_id' in str(message.get('content', ''))
            for message in messages
        )
        if not has_tool_output:
            calls = [{'id': 'r0-get-active', 'type': 'function', 'function': {'name': 'get_active_tasks', 'arguments': '{}'}}]
            message, finish = {'role': 'assistant', 'content': None, 'tool_calls': calls}, 'tool_calls'
        elif not has_proposal_result:
            calls = [
                {'id': f'r0-propose-{index}', 'type': 'function', 'function': {'name': 'propose_task', 'arguments': json.dumps(item, ensure_ascii=False)}}
                for index, item in enumerate(ITEMS, 1)
            ]
            message, finish = {'role': 'assistant', 'content': None, 'tool_calls': calls}, 'tool_calls'
        else:
            message = {'role': 'assistant', 'content': '已根据本次授权检索到的知识卡片 #1 生成两条独立任务草案。日期保持未排期；请在任务工作区编辑并分别审批。'}
            finish = 'stop'
        payload = {
            'id': 'r0-isolated-completion', 'object': 'chat.completion', 'created': 0,
            'model': 'personal-os-r0-mock',
            'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


HTTPServer(('127.0.0.1', int(os.environ.get('R0_MOCK_PORT', '8200'))), Handler).serve_forever()
