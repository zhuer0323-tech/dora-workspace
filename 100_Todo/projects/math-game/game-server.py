#!/usr/bin/env python3
"""
數學冒險遊戲伺服器 — 提供靜態檔案服務 + LINE 報告中轉
用法: python3 game-server.py
瀏覽器開啟: http://localhost:8765
"""
import os, json, subprocess, sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 8765

def load_dora_env():
    env_file = Path.home() / 'Library/Scripts/dora.env'
    env = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"\'')
    return env

_env = load_dora_env()
LINE_TOKEN = _env.get('LINE_PUSH_TOKEN') or os.environ.get('LINE_PUSH_TOKEN', '')
LINE_USER  = _env.get('LINE_USER_ID')   or os.environ.get('LINE_USER_ID', '')

class GameHandler(SimpleHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path == '/api/send-line':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body   = json.loads(self.rfile.read(length))
                text   = body.get('text', '').strip()
            except Exception as e:
                self._json(400, {'error': f'bad request: {e}'})
                return

            if not LINE_TOKEN or not LINE_USER:
                self._json(500, {'error': 'LINE 憑證未設定，請確認 ~/Library/Scripts/dora.env'})
                return

            payload = json.dumps({
                'to': LINE_USER,
                'messages': [{'type': 'text', 'text': text}]
            })
            cmd = [
                'curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                '-X', 'POST',
                'https://api.line.me/v2/bot/message/push',
                '-H', f'Authorization: Bearer {LINE_TOKEN}',
                '-H', 'Content-Type: application/json',
                '-d', payload
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                status_code = result.stdout.strip()
                if status_code == '200':
                    self._json(200, {'ok': True})
                else:
                    self._json(500, {'error': f'LINE API 回傳 {status_code}'})
            except subprocess.TimeoutExpired:
                self._json(500, {'error': '傳送逾時'})
        else:
            self._json(404, {'error': 'not found'})

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', 'http://localhost:8765')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass  # 靜音 access log

if __name__ == '__main__':
    if not LINE_TOKEN:
        print('⚠️  找不到 LINE 憑證，請確認 ~/Library/Scripts/dora.env 存在')
    else:
        print(f'✅ LINE 憑證已載入')

    print(f'🎮 遊戲伺服器啟動中... http://localhost:{PORT}')
    try:
        HTTPServer(('127.0.0.1', PORT), GameHandler).serve_forever()
    except KeyboardInterrupt:
        print('\n伺服器已停止')
        sys.exit(0)
