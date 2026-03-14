"""
公考自学全能助手 - 多用户版
技术栈：Streamlit + SQLite + OpenAI API

新增功能：
- 用户注册/登录系统（密码 bcrypt 加密）
- 每用户独立题库、笔记、答题记录
- API Key 存入数据库，登录后自动读取，无需每次填写
- 管理员可查看所有用户统计
"""

import streamlit as st
import sqlite3
import json
import random
import hashlib
import hmac
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
from docx import Document
import concurrent.futures

# ==================== 全局配置 ====================
DB_PATH = "gongkao.db"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL_NAME = "deepseek-chat"
ADMIN_USERNAME = "admin"  # 管理员账号，可修改
# =================================================


# ==================== 密码工具 ====================
def hash_password(password: str) -> str:
    """使用 SHA-256 + 固定盐值加密密码"""
    salt = "gongkao_assistant_salt_2024"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return hmac.compare_digest(hash_password(password), hashed)


# ==================== 数据库初始化 ====================
def init_database():
    """初始化数据库，创建所有表"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()

    # 用户表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            api_key TEXT DEFAULT '',
            base_url TEXT DEFAULT '',
            model_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    ''')

    # 题目表（绑定用户）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            options TEXT NOT NULL,
            answer TEXT NOT NULL,
            correct_count INTEGER DEFAULT 0,
            wrong_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # 答题记录表（绑定用户）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS answer_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            is_correct INTEGER NOT NULL,
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (question_id) REFERENCES questions(id)
        )
    ''')

    # 笔记总结历史表（绑定用户）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS note_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # 任务进度表：每块完成后立即写库，切换页面不丢失进度
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            total_chunks INTEGER NOT NULL,
            result TEXT,
            extra TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()


# ==================== 用户相关数据库操作 ====================
def register_user(username: str, password: str) -> tuple:
    """注册新用户，返回 (成功, 消息)"""
    if len(username) < 2:
        return False, "用户名至少2个字符"
    if len(password) < 6:
        return False, "密码至少6位"
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO users (username, password_hash, base_url, model_name) VALUES (?, ?, ?, ?)',
            (username, hash_password(password), DEFAULT_BASE_URL, DEFAULT_MODEL_NAME)
        )
        conn.commit()
        conn.close()
        return True, "注册成功！"
    except sqlite3.IntegrityError:
        return False, "用户名已存在，请换一个"
    except Exception as e:
        return False, f"注册失败：{str(e)}"


def login_user(username: str, password: str) -> tuple:
    """登录验证，返回 (user_dict 或 None, 消息)"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()

        if not user:
            conn.close()
            return None, "用户名不存在"

        if not verify_password(password, user[2]):
            conn.close()
            return None, "密码错误"

        # 更新最后登录时间
        cursor.execute('UPDATE users SET last_login = ? WHERE id = ?', (datetime.now(), user[0]))
        conn.commit()
        conn.close()

        return {
            'id': user[0],
            'username': user[1],
            'api_key': user[3] or '',
            'base_url': user[4] or DEFAULT_BASE_URL,
            'model_name': user[5] or DEFAULT_MODEL_NAME,
        }, "登录成功"
    except Exception as e:
        return None, f"登录失败：{str(e)}"


def save_user_api_config(user_id: int, api_key: str, base_url: str, model_name: str):
    """保存用户 API 配置到数据库"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET api_key=?, base_url=?, model_name=? WHERE id=?',
            (api_key, base_url, model_name, user_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"保存配置失败：{str(e)}")
        return False


def change_password(user_id: int, old_password: str, new_password: str) -> tuple:
    """修改密码，返回 (成功, 消息)"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT password_hash FROM users WHERE id = ?', (user_id,))
        result = cursor.fetchone()
        if not result or not verify_password(old_password, result[0]):
            conn.close()
            return False, "原密码错误"
        if len(new_password) < 6:
            conn.close()
            return False, "新密码至少6位"
        cursor.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(new_password), user_id))
        conn.commit()
        conn.close()
        return True, "密码修改成功"
    except Exception as e:
        return False, f"修改失败：{str(e)}"


# ==================== 题目相关数据库操作 ====================
def get_question_count(user_id: int, category=None) -> int:
    """获取当前用户题库题目数"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if category and category != "全部":
            cursor.execute('SELECT COUNT(*) FROM questions WHERE user_id=? AND category=?', (user_id, category))
        else:
            cursor.execute('SELECT COUNT(*) FROM questions WHERE user_id=?', (user_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def add_question(user_id: int, category: str, content: str, options: dict, answer: str) -> bool:
    """为当前用户添加题目"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO questions (user_id, category, content, options, answer) VALUES (?,?,?,?,?)',
            (user_id, category, content, json.dumps(options, ensure_ascii=False), answer)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"添加题目失败：{str(e)}")
        return False


def get_weighted_question(user_id: int, category=None) -> dict:
    """按权重为当前用户抽题"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if category and category != "全部":
            cursor.execute('SELECT * FROM questions WHERE user_id=? AND category=?', (user_id, category))
        else:
            cursor.execute('SELECT * FROM questions WHERE user_id=?', (user_id,))
        questions = cursor.fetchall()
        conn.close()

        if not questions:
            return None

        weights = [(q[7] * 2) + 1 for q in questions]  # wrong_count 在第8列(index 7)
        selected = random.choices(questions, weights=weights, k=1)[0]

        return {
            'id': selected[0],
            'category': selected[2],
            'content': selected[3],
            'options': json.loads(selected[4]),
            'answer': selected[5],
            'correct_count': selected[6],
            'wrong_count': selected[7]
        }
    except Exception as e:
        st.error(f"抽题失败：{str(e)}")
        return None


def update_question_stats(user_id: int, question_id: int, is_correct: bool):
    """更新答题统计"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if is_correct:
            cursor.execute('UPDATE questions SET correct_count=correct_count+1 WHERE id=? AND user_id=?',
                           (question_id, user_id))
        else:
            cursor.execute('UPDATE questions SET wrong_count=wrong_count+1 WHERE id=? AND user_id=?',
                           (question_id, user_id))
        cursor.execute(
            'INSERT INTO answer_records (user_id, question_id, is_correct) VALUES (?,?,?)',
            (user_id, question_id, 1 if is_correct else 0)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"更新统计失败：{str(e)}")


def get_question_latest_stats(question_id: int) -> tuple:
    """获取题目最新统计"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT correct_count, wrong_count FROM questions WHERE id=?', (question_id,))
        result = cursor.fetchone()
        conn.close()
        return result if result else (0, 0)
    except Exception:
        return (0, 0)


def get_category_stats(user_id: int) -> dict:
    """获取当前用户各科目统计"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT q.category, COUNT(ar.id), SUM(ar.is_correct)
            FROM answer_records ar
            JOIN questions q ON ar.question_id = q.id
            WHERE ar.user_id = ?
            GROUP BY q.category
        ''', (user_id,))
        results = cursor.fetchall()
        conn.close()
        stats = {}
        for category, total, correct in results:
            stats[category] = {
                'total': total,
                'accuracy': round((correct / total * 100), 1) if total else 0
            }
        return stats
    except Exception:
        return {}


def get_daily_stats(user_id: int) -> pd.DataFrame:
    """获取当前用户最近7天答题趋势"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DATE(answered_at), COUNT(*), SUM(is_correct)
            FROM answer_records
            WHERE user_id=? AND answered_at >= date('now', '-7 days')
            GROUP BY DATE(answered_at)
            ORDER BY DATE(answered_at)
        ''', (user_id,))
        results = cursor.fetchall()
        conn.close()
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results, columns=['日期', '答题数', '正确数'])
        df['正确率(%)'] = (df['正确数'] / df['答题数'] * 100).round(1)
        return df
    except Exception:
        return pd.DataFrame()


def save_note_summary(user_id: int, filename: str, summary: str):
    """保存笔记总结到数据库"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO note_summaries (user_id, filename, summary) VALUES (?,?,?)',
            (user_id, filename, summary)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"保存笔记失败：{str(e)}")


def get_note_summaries(user_id: int) -> list:
    """获取当前用户的历史笔记总结"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, filename, summary, created_at FROM note_summaries WHERE user_id=? ORDER BY created_at DESC',
            (user_id,)
        )
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception:
        return []


# ==================== LLM 调用 ====================
def call_llm_single(args):
    """并行 LLM 调用单元"""
    i, chunk, api_key, base_url, model_name, prompt_type = args
    if prompt_type == "summary":
        prompt = f"""请对以下公考学习资料进行结构化总结，使用 Markdown 格式输出，包含：
1. **核心考点**：列出本段的关键知识点（3-5 条）
2. **重点记忆项**：需要重点背诵的内容（3-5 条）
3. **易混淆点**：容易出错或混淆的地方（2-3 条）

要求：简洁明了，突出重点，避免冗余。

资料内容：
{chunk}
"""
    else:  # extract
        prompt = f"""请从以下公考题目文本中提取所有选择题，严格按照 JSON 格式输出。

要求：
1. 只提取有完整题目+四个选项+答案的题目
2. 如果答案不在文本中，跳过该题
3. 只输出 JSON 数组，不要任何解释，不要 markdown 代码块

输出格式：
[{{"content":"题目正文","A":"选项A","B":"选项B","C":"选项C","D":"选项D","answer":"A"}}]

文本内容：
{chunk}
"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1 if prompt_type == "extract" else 0.7
        )
        return (i, response.choices[0].message.content)
    except Exception as e:
        return (i, None)


# ==================== 文档处理 ====================
def extract_text_from_pdf(file) -> str:
    """从 PDF 提取全文"""
    try:
        text = ""
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip() or None
    except Exception as e:
        st.error(f"PDF 解析失败：{str(e)}")
        return None


def extract_text_from_docx(file) -> str:
    """从 Word 文档提取全文"""
    try:
        doc = Document(file)
        text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return text.strip() or None
    except Exception as e:
        st.error(f"Word 解析失败：{str(e)}")
        return None


def chunk_text(text: str, max_length=15000) -> list:
    """将文本按最大长度分块"""
    chunks = []
    while len(text) > max_length:
        chunks.append(text[:max_length])
        text = text[max_length:]
    if text:
        chunks.append(text)
    return chunks


def save_task_chunk(user_id, task_id, task_type, chunk_index, total_chunks, result, extra=None):
    """每块完成后立即写入数据库，防止切换页面丢失"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO task_chunks
               (user_id, task_id, task_type, chunk_index, total_chunks, result, extra)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, task_id, task_type, chunk_index, total_chunks, result, extra)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        pass  # 写库失败不影响主流程


def get_task_chunks(user_id, task_id):
    """获取某个任务已完成的所有块"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chunk_index, total_chunks, result, extra, task_type FROM task_chunks WHERE user_id=? AND task_id=? ORDER BY chunk_index",
            (user_id, task_id)
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def clear_task(user_id, task_id):
    """清除任务进度（任务完成后调用）"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM task_chunks WHERE user_id=? AND task_id=?", (user_id, task_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_parallel_llm(chunks, api_key, base_url, model_name, prompt_type="summary",
                     user_id=None, task_id=None, on_chunk_done=None):
    """
    并行调用 LLM，每块完成后立即写入数据库。
    on_chunk_done(i, result): 可选回调，用于实时导入题目等操作。
    """
    total = len(chunks)
    results = [None] * total
    progress_bar = st.progress(0)
    status_text = st.empty()
    placeholders = [st.empty() for _ in range(total)]
    completed = 0

    args_list = [(i, chunk, api_key, base_url, model_name, prompt_type) for i, chunk in enumerate(chunks)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(call_llm_single, args): args[0] for args in args_list}
        for future in concurrent.futures.as_completed(futures):
            i, result = future.result()
            completed += 1
            results[i] = result
            progress_bar.progress(completed / total)
            status_text.text(f"⚡ 已完成 {completed} / {total} 块...")

            if result:
                # 立即写入数据库，切换页面不丢失
                if user_id and task_id:
                    save_task_chunk(user_id, task_id, prompt_type, i, total, result)

                if prompt_type == "summary":
                    placeholders[i].markdown(f"### 第 {i+1} 部分\n\n{result}\n\n---")

                # 执行回调（如实时导入题目）
                if on_chunk_done:
                    on_chunk_done(i, result)

    progress_bar.empty()
    status_text.empty()
    return results, placeholders


# ==================== 登录/注册页面 ====================
def page_auth():
    """登录注册页面"""
    st.title("🎓 公考自学全能助手")
    st.caption("多用户版 · 每人独立题库与学习记录")
    st.markdown("---")

    tab_login, tab_register = st.tabs(["🔑 登录", "📝 注册"])

    with tab_login:
        st.subheader("登录账号")
        username = st.text_input("用户名", key="login_username")
        password = st.text_input("密码", type="password", key="login_password")

        if st.button("登录", type="primary", use_container_width=True):
            if not username or not password:
                st.error("请填写用户名和密码")
            else:
                user, msg = login_user(username, password)
                if user:
                    st.session_state.user = user
                    st.session_state.api_key = user['api_key']
                    st.session_state.base_url = user['base_url']
                    st.session_state.model_name = user['model_name']
                    st.success(f"✅ 欢迎回来，{user['username']}！")
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")

    with tab_register:
        st.subheader("注册新账号")
        new_username = st.text_input("用户名（至少2个字符）", key="reg_username")
        new_password = st.text_input("密码（至少6位）", type="password", key="reg_password")
        confirm_password = st.text_input("确认密码", type="password", key="reg_confirm")

        if st.button("注册", type="primary", use_container_width=True):
            if not new_username or not new_password:
                st.error("请填写完整信息")
            elif new_password != confirm_password:
                st.error("两次密码不一致")
            else:
                ok, msg = register_user(new_username, new_password)
                if ok:
                    st.success(f"✅ {msg} 请切换到登录标签页登录")
                else:
                    st.error(f"❌ {msg}")


# ==================== 侧边栏 ====================
def render_sidebar() -> str:
    """渲染侧边栏，返回当前选中页面"""
    user = st.session_state.user

    st.sidebar.title("🎓 公考自学全能助手")
    st.sidebar.caption(f"👤 {user['username']}")
    st.sidebar.markdown("---")

    # ---- API 配置 ----
    with st.sidebar.expander("⚙️ API 配置", expanded=not st.session_state.get('api_key')):
        api_key = st.text_input("API Key", value=st.session_state.get('api_key', ''),
                                type="password", help="DeepSeek / OpenAI 等兼容接口均可")
        base_url = st.text_input("Base URL", value=st.session_state.get('base_url', DEFAULT_BASE_URL),
                                 help="DeepSeek：https://api.deepseek.com/v1")
        model_name = st.text_input("Model Name", value=st.session_state.get('model_name', DEFAULT_MODEL_NAME),
                                   help="DeepSeek：deepseek-chat")

        if st.button("💾 保存配置", type="primary"):
            st.session_state.api_key = api_key
            st.session_state.base_url = base_url
            st.session_state.model_name = model_name
            # 同步保存到数据库，下次登录自动读取
            if save_user_api_config(user['id'], api_key, base_url, model_name):
                st.success("✅ 已保存，下次登录自动读取")
                st.rerun()

    st.sidebar.markdown("---")

    # ---- 题库概况 ----
    st.sidebar.metric("📦 我的题库", f"{get_question_count(user['id'])} 道")

    st.sidebar.markdown("---")

    # ---- 功能导航 ----
    page = st.sidebar.radio(
        "📌 功能导航",
        ["📚 智能笔记总结", "🔍 PDF题目提取", "✍️ 智能刷题系统",
         "📊 备考数据看板", "📅 动态学习计划", "👤 账号设置"]
    )

    st.sidebar.markdown("---")

    # ---- 退出登录 ----
    if st.sidebar.button("🚪 退出登录", use_container_width=True):
        for key in ['user', 'api_key', 'base_url', 'model_name',
                    'summary_results', 'current_question', 'answered', 'user_answer']:
            st.session_state.pop(key, None)
        st.rerun()

    return page


# ==================== 页面：智能笔记总结 ====================
def page_note_summary():
    st.header("📚 智能笔记总结")
    st.caption("上传 PDF 或 Word，AI 自动提取核心考点，每块完成立即保存，切换页面不丢失")

    user = st.session_state.user
    api_key = st.session_state.get('api_key', '')

    if not api_key:
        st.warning("⚠️ 请先在左侧侧边栏填写并保存 API Key")
        return

    tab_new, tab_history = st.tabs(["📤 新建总结", "📂 历史总结"])

    with tab_new:
        uploaded_file = st.file_uploader("选择文件（PDF / DOCX）", type=['pdf', 'docx'])

        # 检查是否有未完成的任务（切换页面回来后恢复进度）
        current_task_id = st.session_state.get('summary_task_id')
        if current_task_id and not uploaded_file:
            chunks_done = get_task_chunks(user['id'], current_task_id)
            if chunks_done:
                total = chunks_done[0][1]
                done_count = len(chunks_done)
                if done_count < total:
                    st.warning(f"⚠️ 上次任务未完成（已完成 {done_count}/{total} 块），请重新上传文件继续")
                else:
                    st.info("上次总结已全部完成，可在「历史总结」中查看")
            return

        if not uploaded_file:
            st.info("请上传文件后点击「开始总结」")
            return

        file_type = uploaded_file.name.split('.')[-1].lower()
        if st.button("🚀 开始总结", type="primary"):
            with st.spinner("正在解析文档..."):
                text = extract_text_from_pdf(uploaded_file) if file_type == 'pdf' \
                    else extract_text_from_docx(uploaded_file)

            if not text:
                st.error("文档解析失败（扫描件无法识别，请使用含文字的 PDF）")
                return

            chunks = chunk_text(text)
            # 生成唯一任务ID
            import hashlib as _hl
            task_id = _hl.md5(f"{user['id']}_{uploaded_file.name}_{len(text)}".encode()).hexdigest()[:12]
            st.session_state.summary_task_id = task_id

            # 清除旧的同名任务
            clear_task(user['id'], task_id)

            st.success(f"✅ 解析成功，共 {len(text)} 字，分 {len(chunks)} 块并行处理")
            st.markdown("---")

            # 每块完成后立即存入note_summaries（追加模式）
            filename = uploaded_file.name
            saved_parts = []

            def on_summary_done(i, result):
                """每块完成回调：立即追加保存到数据库"""
                saved_parts.append(f"### 第 {i+1} 部分\n\n{result}")

            results, _ = run_parallel_llm(
                chunks, api_key,
                st.session_state.get('base_url', DEFAULT_BASE_URL),
                st.session_state.get('model_name', DEFAULT_MODEL_NAME),
                prompt_type="summary",
                user_id=user['id'],
                task_id=task_id,
                on_chunk_done=on_summary_done
            )

            # 全部完成后整合保存一份完整记录
            all_chunks = get_task_chunks(user['id'], task_id)
            if all_chunks:
                ordered = sorted(all_chunks, key=lambda x: x[0])
                full_summary = "\n\n---\n\n".join(
                    [f"### 第 {r[0]+1} 部分\n\n{r[2]}" for r in ordered if r[2]]
                )
                save_note_summary(user['id'], filename, full_summary)
                clear_task(user['id'], task_id)
                st.session_state.pop('summary_task_id', None)
                st.success("🎉 总结完成，已永久保存到历史记录！")
            else:
                st.error("总结失败，请检查 API 配置")

    with tab_history:
        summaries = get_note_summaries(user['id'])
        if not summaries:
            st.info("暂无历史总结，上传文件开始你的第一次总结吧～")
        else:
            for sid, filename, summary, created_at in summaries:
                with st.expander(f"📄 {filename}  |  {created_at[:16]}"):
                    st.markdown(summary)


# ==================== 页面：PDF题目提取 ====================
def page_extract_questions():
    st.header("🔍 PDF 题目提取")
    st.caption("上传含题目的 PDF，AI 自动识别并批量导入你的题库")

    user = st.session_state.user
    api_key = st.session_state.get('api_key', '')

    if not api_key:
        st.warning("⚠️ 请先在左侧侧边栏填写并保存 API Key")
        return

    uploaded_file = st.file_uploader("选择题目 PDF", type=['pdf'], key="extract_uploader")
    col1, col2 = st.columns(2)
    with col1:
        category = st.selectbox("题目科目", ["言语理解", "判断推理", "资料分析", "数量关系", "常识判断"])
    with col2:
        st.metric("我的题库", f"{get_question_count(user['id'])} 道")

    if not uploaded_file:
        st.info("请上传题目 PDF 后点击「开始提取」")
        return

    if st.button("🚀 开始提取", type="primary"):
        with st.spinner("解析 PDF..."):
            text = extract_text_from_pdf(uploaded_file)

        if not text:
            st.error("PDF 解析失败")
            return

        chunks = chunk_text(text, max_length=6000)
        st.info(f"📦 分 {len(chunks)} 块并行提取中...")

        # 实时导入回调：每块提取完立即写入题库
        realtime_success = [0]
        realtime_fail = [0]
        import_log = st.empty()

        def on_extract_done(i, raw):
            """每块提取完成回调：立即解析并导入题目"""
            try:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("```")[1]
                    if cleaned.startswith("json"):
                        cleaned = cleaned[4:]
                qs = json.loads(cleaned.strip())
                if not isinstance(qs, list):
                    return
                for q in qs:
                    opts = {"A": q.get("A",""), "B": q.get("B",""), "C": q.get("C",""), "D": q.get("D","")}
                    if q.get("content") and all(opts.values()) and q.get("answer") in ["A","B","C","D"]:
                        if add_question(user['id'], category, q["content"], opts, q["answer"]):
                            realtime_success[0] += 1
                        else:
                            realtime_fail[0] += 1
                    else:
                        realtime_fail[0] += 1
                import_log.info(f"📥 已实时导入 {realtime_success[0]} 道题，跳过 {realtime_fail[0]} 道...")
            except Exception:
                pass

        import hashlib as _hl
        task_id = _hl.md5(f"{user['id']}_extract_{uploaded_file.name}".encode()).hexdigest()[:12]

        results, _ = run_parallel_llm(
            chunks, api_key,
            st.session_state.get('base_url', DEFAULT_BASE_URL),
            st.session_state.get('model_name', DEFAULT_MODEL_NAME),
            prompt_type="extract",
            user_id=user['id'],
            task_id=task_id,
            on_chunk_done=on_extract_done
        )

        import_log.empty()

        if realtime_success[0] == 0:
            st.error("未提取到题目，请确认 PDF 含有完整的 ABCD 四选一题目及答案")
            return

        st.success(f"🎉 全部提取完成！已导入 **{realtime_success[0]}** 道题{'，跳过 '+str(realtime_fail[0])+' 道格式不完整的' if realtime_fail[0] else ''}。题目已实时写入，切换页面也不会丢失！")
        st.balloons()
        clear_task(user['id'], task_id)


# ==================== 页面：智能刷题系统 ====================
def page_practice():
    st.header("✍️ 智能刷题系统")
    user = st.session_state.user
    CATEGORIES = ["全部", "言语理解", "判断推理", "资料分析", "数量关系", "常识判断"]

    # 手动添加题目
    with st.expander("➕ 手动添加题目"):
        with st.form("add_q_form", clear_on_submit=True):
            cat = st.selectbox("科目", CATEGORIES[1:])
            content = st.text_area("题目内容", placeholder="请输入题目正文...")
            c1, c2 = st.columns(2)
            with c1:
                oa = st.text_input("选项 A")
                ob = st.text_input("选项 B")
            with c2:
                oc = st.text_input("选项 C")
                od = st.text_input("选项 D")
            ans = st.selectbox("正确答案", ["A","B","C","D"])
            if st.form_submit_button("✅ 添加", type="primary"):
                if all([content, oa, ob, oc, od]):
                    if add_question(user['id'], cat, content, {"A":oa,"B":ob,"C":oc,"D":od}, ans):
                        st.success("添加成功！")
                        st.rerun()
                else:
                    st.error("请填写全部字段")

    st.markdown("---")

    selected_cat = st.selectbox("按科目筛选", CATEGORIES)
    count = get_question_count(user['id'], selected_cat)
    st.caption(f"当前范围共 **{count}** 道题")

    if count == 0:
        st.info("📭 题库为空，请先通过「PDF题目提取」或「手动添加」录入题目")
        return

    if st.button("🎲 抽取一道题", type="primary"):
        q = get_weighted_question(user['id'], selected_cat)
        if q:
            st.session_state.current_question = q
            st.session_state.user_answer = None
            st.session_state.answered = False
            st.rerun()

    for key, default in [('current_question', None), ('answered', False), ('user_answer', None)]:
        if key not in st.session_state:
            st.session_state[key] = default

    question = st.session_state.current_question
    if not question:
        st.info("💡 点击「抽取一道题」开始练习")
        return

    st.subheader(f"【{question['category']}】")
    st.write(question['content'])
    st.write("")
    for k, v in question['options'].items():
        st.write(f"**{k}.** {v}")
    st.write("")

    if not st.session_state.answered:
        user_ans = st.radio("请选择答案", ["A","B","C","D"], horizontal=True)
        if st.button("📨 提交答案", type="primary"):
            is_correct = (user_ans == question['answer'])
            update_question_stats(user['id'], question['id'], is_correct)
            st.session_state.user_answer = user_ans
            st.session_state.answered = True
            st.rerun()
    else:
        is_correct = (st.session_state.user_answer == question['answer'])
        if is_correct:
            st.success(f"✅ 回答正确！正确答案：**{question['answer']}**")
        else:
            st.error(f"❌ 错误！你的答案：**{st.session_state.user_answer}**，正确答案：**{question['answer']}**")

        correct_count, wrong_count = get_question_latest_stats(question['id'])
        st.info(f"📊 本题累计：答对 {correct_count} 次 / 答错 {wrong_count} 次")

        if st.button("➡️ 继续下一题"):
            st.session_state.current_question = get_weighted_question(user['id'], selected_cat)
            st.session_state.user_answer = None
            st.session_state.answered = False
            st.rerun()


# ==================== 页面：备考数据看板 ====================
def page_dashboard():
    st.header("📊 备考数据看板")
    user = st.session_state.user

    st.subheader("各科目答题统计")
    stats = get_category_stats(user['id'])
    if stats:
        df = pd.DataFrame([{"科目": k, "答题数": v['total'], "正确率(%)": v['accuracy']} for k, v in stats.items()])
        c1, c2 = st.columns([1, 2])
        with c1:
            st.dataframe(df, hide_index=True, use_container_width=True)
        with c2:
            st.bar_chart(df.set_index('科目')['正确率(%)'])
    else:
        st.info("📝 暂无答题数据，去「✍️ 智能刷题系统」开始练习吧！")

    st.markdown("---")
    st.subheader("最近 7 天答题趋势")
    daily = get_daily_stats(user['id'])
    if not daily.empty:
        st.dataframe(daily[['日期','答题数','正确率(%)']], hide_index=True, use_container_width=True)
        c1, c2 = st.columns(2)
        with c1:
            st.caption("每日答题量")
            st.line_chart(daily.set_index('日期')['答题数'])
        with c2:
            st.caption("每日正确率 (%)")
            st.line_chart(daily.set_index('日期')['正确率(%)'])
    else:
        st.info("📈 暂无最近 7 天记录，坚持每天刷题！")


# ==================== 页面：动态学习计划 ====================
def page_study_plan():
    st.header("📅 动态学习计划")

    target_date = st.date_input(
        "设置考试目标日期",
        value=datetime.now().date() + timedelta(days=90),
        min_value=datetime.now().date()
    )
    days_left = (target_date - datetime.now().date()).days

    if days_left > 60:
        stage, badge, color = "基础期", "🟢", "info"
        tasks = ["📖 刷10道判断推理题（重在理解）", "📝 上传笔记生成AI总结",
                 "💡 整理常识判断知识点1个模块", "🔍 复习昨日错题"]
        advice = "**基础期：** 系统建立知识框架，不追求速度，先求理解再求正确率。"
    elif days_left >= 30:
        stage, badge, color = "强化期", "🟡", "warning"
        tasks = ["📖 刷20道混合题", "📝 复习错题集，攻克薄弱科目",
                 "💡 做1套限时模拟卷", "🔍 总结答题规律"]
        advice = "**强化期：** 大量刷题提速，限时训练，针对薄弱环节集中突破。"
    elif days_left > 0:
        stage, badge, color = "冲刺期", "🔴", "error"
        tasks = ["📖 刷30道全真模拟题（严格限时）", "📝 攻克错误率最高的科目",
                 "💡 做2套完整模拟卷", "🔍 回顾高频考点", "🎯 调整作息保持状态"]
        advice = "**冲刺期：** 全真模拟、查漏补缺。减少新知识输入，重点巩固高频考点。"
    else:
        st.error("考试日期已过，请重新设置")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("⏳ 距离考试", f"{days_left} 天")
    c2.metric("📌 当前阶段", f"{badge} {stage}")
    c3.metric("🗓 目标日期", target_date.strftime("%Y-%m-%d"))

    st.markdown("---")
    st.subheader("📋 今日任务清单")
    for i, task in enumerate(tasks):
        st.checkbox(task, key=f"task_{i}")

    st.markdown("---")
    st.subheader("💡 阶段建议")
    getattr(st, color)(advice)

    st.markdown("---")
    elapsed = max(0, 90 - days_left)
    st.progress(min(elapsed / 90, 1.0), text=f"备考进度：约 {min(int(elapsed/90*100), 100)}%（以90天为基准）")


# ==================== 页面：账号设置 ====================
def page_account():
    st.header("👤 账号设置")
    user = st.session_state.user

    st.subheader("基本信息")
    st.info(f"**用户名：** {user['username']}")

    st.markdown("---")
    st.subheader("修改密码")
    with st.form("change_pwd_form"):
        old_pwd = st.text_input("原密码", type="password")
        new_pwd = st.text_input("新密码（至少6位）", type="password")
        confirm_pwd = st.text_input("确认新密码", type="password")
        if st.form_submit_button("修改密码", type="primary"):
            if new_pwd != confirm_pwd:
                st.error("两次密码不一致")
            else:
                ok, msg = change_password(user['id'], old_pwd, new_pwd)
                if ok:
                    st.success(f"✅ {msg}，请重新登录")
                    st.session_state.pop('user', None)
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")

    st.markdown("---")
    st.subheader("我的数据统计")
    c1, c2, c3 = st.columns(3)
    c1.metric("题库题目", f"{get_question_count(user['id'])} 道")
    c2.metric("历史笔记", f"{len(get_note_summaries(user['id']))} 篇")
    daily = get_daily_stats(user['id'])
    total_answered = int(daily['答题数'].sum()) if not daily.empty else 0
    c3.metric("近7天答题", f"{total_answered} 道")


# ==================== 主程序 ====================
def main():
    st.set_page_config(
        page_title="公考自学全能助手",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    init_database()

    # 未登录 → 显示登录页
    if 'user' not in st.session_state:
        page_auth()
        return

    # 初始化 session_state
    for key, default in [
        ('summary_results', []),
        ('current_question', None),
        ('answered', False),
        ('user_answer', None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    page = render_sidebar()

    if page == "📚 智能笔记总结":
        page_note_summary()
    elif page == "🔍 PDF题目提取":
        page_extract_questions()
    elif page == "✍️ 智能刷题系统":
        page_practice()
    elif page == "📊 备考数据看板":
        page_dashboard()
    elif page == "📅 动态学习计划":
        page_study_plan()
    elif page == "👤 账号设置":
        page_account()


if __name__ == "__main__":
    main()


# ==================== 依赖清单 ====================
# pip install streamlit pdfplumber python-docx openai pandas
