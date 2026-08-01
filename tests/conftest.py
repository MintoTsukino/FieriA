"""全テスト共通: fieria_home を一時ディレクトリへ差し替える。
NikoVoiceで実config.jsonをテストが上書きした事故（README設計判断33番）の再発防止。
import config より前に環境変数で差し替える方式。"""
import os
import tempfile

os.environ["FIERIA_HOME"] = tempfile.mkdtemp(prefix="fieria-test-")
# gui.Bridge()生成時にSchedulerのdaemonスレッドを起動させない環境変数ガード。
# 2026-07-22以降、.env/xai_oauth.json/openai_codex_oauth.jsonはFIERIA_HOME直下へ
# 移設されFIERIA_HOME隔離の対象に入ったが、それでも実.env等に一切触れないよう
# env.py/xai_oauth.py/openai_codex_oauth.pyがFIERIA_TESTINGを見て起動時の実ファイル
# 移行チェック自体をスキップする（テストからの実LLM到達を60秒の時限レース頼みではなく
# 構造的に遮断する、という元の思想を維持）。
os.environ["FIERIA_TESTING"] = "1"
