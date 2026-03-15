"""
AI 自学全能助手 - 多用户版
技术栈：Streamlit + SQLite + OpenAI API

功能：
- 用户注册/登录，每人独立数据
- 应用名称、科目完全自定义
- 智能笔记总结（并行处理+实时进度）
- PDF题目提取（实时写库）
- 权重刷题系统（题目导出/删除）
- 备考数据看板
- 动态学习计划
"""

import streamlit as st
import sqlite3
import json
import random
import hashlib
import hmac
import io
import csv
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
from docx import Document
import concurrent.futures

# ==================== 全局配置 ====================
DB_PATH = "study.db"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL_NAME = "deepseek-chat"
DEFAULT_APP_NAME = "AI 自学全能助手"
DEFAULT_SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "历史", "地理", "生物", "政治", "其他"]
# =================================================


# ==================== 全局 CSS 美化 ====================
def inject_css():
    st.markdown("""
    <style>
    /* 主背景 */
    .main { background-color: #f8f9fc; }

    /* 顶部标题栏 */
    .app-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 15px rgba(102,126,234,0.3);
    }
    .app-header h1 { color: white !important; margin: 0; font-size: 1.8rem; }
    .app-header p { color: rgba(255,255,255,0.85); margin: 0.3rem 0 0 0; font-size: 0.9rem; }

    /* 统计卡片 */
    .stat-card {
        background: white;
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        border-left: 4px solid #667eea;
        margin-bottom: 1rem;
    }
    .stat-card .value { font-size: 2rem; font-weight: 700; color: #667eea; }
    .stat-card .label { font-size: 0.85rem; color: #888; margin-top: 0.2rem; }

    /* 题目卡片 */
    .question-card {
        background: white;
        border-radius: 12px;
        padding: 1.5rem;
        box-shadow: 0 2px 12px rgba(0,0,0,0.07);
        margin-bottom: 1rem;
        border-top: 3px solid #667eea;
    }

    /* 进度条美化 */
    .stProgress > div > div { background: linear-gradient(90deg, #667eea, #764ba2); border-radius: 10px; }

    /* 按钮美化 */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #667eea, #764ba2);
        border: none;
        border-radius: 8px;
        box-shadow: 0 3px 10px rgba(102,126,234,0.3);
        transition: all 0.2s;
    }
    .stButton > button[kind="primary"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 5px 15px rgba(102,126,234,0.4);
    }

    /* 侧边栏美化 */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
    }
    [data-testid="stSidebar"] * { color: #e0e0e0 !important; }
    [data-testid="stSidebar"] .stRadio label { 
        padding: 0.4rem 0.8rem;
        border-radius: 6px;
        transition: background 0.2s;
    }

    /* 成功/错误提示美化 */
    .success-banner {
        background: linear-gradient(135deg, #11998e, #38ef7d);
        color: white;
        padding: 1rem 1.5rem;
        border-radius: 10px;
        font-weight: 600;
        margin: 1rem 0;
    }
    .error-banner {
        background: linear-gradient(135deg, #ff416c, #ff4b2b);
        color: white;
        padding: 1rem 1.5rem;
        border-radius: 10px;
        font-weight: 600;
        margin: 1rem 0;
    }

    /* 标签页美化 */
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 0.5rem 1.2rem;
    }

    /* 隐藏 Streamlit 默认菜单 */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    st.markdown(f"""
    <div class="app-header">
        <h1>{title}</h1>
        {"<p>" + subtitle + "</p>" if subtitle else ""}
    </div>
    """, unsafe_allow_html=True)


def stat_card(value, label, col=None):
    html = f"""<div class="stat-card"><div class="value">{value}</div><div class="label">{label}</div></div>"""
    if col:
        col.markdown(html, unsafe_allow_html=True)
    else:
        st.markdown(html, unsafe_allow_html=True)


# ==================== 密码工具 ====================
def hash_password(password: str) -> str:
    salt = "study_assistant_salt_2024"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password), hashed)


# ==================== 数据库初始化 ====================
def init_database():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()

    # 用户表（含应用个性化设置）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            api_key TEXT DEFAULT '',
            base_url TEXT DEFAULT '',
            model_name TEXT DEFAULT '',
            app_name TEXT DEFAULT '',
            subjects TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    ''')

    # 题目表
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

    # 答题记录表
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

    # 笔记总结历史表
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

    # 任务进度表（实时写库，防切页丢失）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            total_chunks INTEGER NOT NULL,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()


# ==================== 用户操作 ====================
def register_user(username, password):
    if len(username) < 2: return False, "用户名至少2个字符"
    if len(password) < 6: return False, "密码至少6位"
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO users (username, password_hash, base_url, model_name) VALUES (?,?,?,?)',
            (username, hash_password(password), DEFAULT_BASE_URL, DEFAULT_MODEL_NAME)
        )
        conn.commit()
        conn.close()
        return True, "注册成功！"
    except sqlite3.IntegrityError:
        return False, "用户名已存在"
    except Exception as e:
        return False, str(e)


def login_user(username, password):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username=?', (username,))
        user = cursor.fetchone()
        if not user: return None, "用户名不存在"
        if not verify_password(password, user[2]): return None, "密码错误"
        cursor.execute('UPDATE users SET last_login=? WHERE id=?', (datetime.now(), user[0]))
        conn.commit()
        conn.close()
        subjects = json.loads(user[8]) if user[8] else DEFAULT_SUBJECTS
        return {
            'id': user[0], 'username': user[1],
            'api_key': user[3] or '', 'base_url': user[4] or DEFAULT_BASE_URL,
            'model_name': user[5] or DEFAULT_MODEL_NAME,
            'app_name': user[6] or DEFAULT_APP_NAME,
            'subjects': subjects,
        }, "登录成功"
    except Exception as e:
        return None, str(e)


def save_user_settings(user_id, api_key, base_url, model_name, app_name, subjects):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET api_key=?,base_url=?,model_name=?,app_name=?,subjects=? WHERE id=?',
            (api_key, base_url, model_name, app_name, json.dumps(subjects, ensure_ascii=False), user_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(str(e))
        return False


def change_password(user_id, old_pwd, new_pwd):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT password_hash FROM users WHERE id=?', (user_id,))
        result = cursor.fetchone()
        if not result or not verify_password(old_pwd, result[0]):
            conn.close()
            return False, "原密码错误"
        if len(new_pwd) < 6:
            conn.close()
            return False, "新密码至少6位"
        cursor.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(new_pwd), user_id))
        conn.commit()
        conn.close()
        return True, "密码修改成功"
    except Exception as e:
        return False, str(e)


# ==================== 题目操作 ====================
def get_question_count(user_id, category=None):
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


def add_question(user_id, category, content, options, answer):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO questions (user_id,category,content,options,answer) VALUES (?,?,?,?,?)',
            (user_id, category, content, json.dumps(options, ensure_ascii=False), answer)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        return False


def delete_question(user_id, question_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM questions WHERE id=? AND user_id=?', (question_id, user_id))
        cursor.execute('DELETE FROM answer_records WHERE question_id=? AND user_id=?', (question_id, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_all_questions(user_id, category=None):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if category and category != "全部":
            cursor.execute('DELETE FROM questions WHERE user_id=? AND category=?', (user_id, category))
        else:
            cursor.execute('DELETE FROM questions WHERE user_id=?', (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_all_questions(user_id, category=None):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if category and category != "全部":
            cursor.execute('SELECT * FROM questions WHERE user_id=? AND category=? ORDER BY created_at DESC',
                           (user_id, category))
        else:
            cursor.execute('SELECT * FROM questions WHERE user_id=? ORDER BY created_at DESC', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_weighted_question(user_id, category=None):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if category and category != "全部":
            cursor.execute('SELECT * FROM questions WHERE user_id=? AND category=?', (user_id, category))
        else:
            cursor.execute('SELECT * FROM questions WHERE user_id=?', (user_id,))
        questions = cursor.fetchall()
        conn.close()
        if not questions: return None
        weights = [(q[7] * 2) + 1 for q in questions]
        selected = random.choices(questions, weights=weights, k=1)[0]
        return {
            'id': selected[0], 'category': selected[2],
            'content': selected[3], 'options': json.loads(selected[4]),
            'answer': selected[5], 'correct_count': selected[6], 'wrong_count': selected[7]
        }
    except Exception as e:
        st.error(str(e))
        return None


def update_question_stats(user_id, question_id, is_correct):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if is_correct:
            cursor.execute('UPDATE questions SET correct_count=correct_count+1 WHERE id=? AND user_id=?',
                           (question_id, user_id))
        else:
            cursor.execute('UPDATE questions SET wrong_count=wrong_count+1 WHERE id=? AND user_id=?',
                           (question_id, user_id))
        cursor.execute('INSERT INTO answer_records (user_id,question_id,is_correct) VALUES (?,?,?)',
                       (user_id, question_id, 1 if is_correct else 0))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(str(e))


def get_question_latest_stats(question_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT correct_count, wrong_count FROM questions WHERE id=?', (question_id,))
        result = cursor.fetchone()
        conn.close()
        return result if result else (0, 0)
    except Exception:
        return (0, 0)


def export_questions_csv(user_id, category=None) -> str:
    """导出题目为 CSV 字符串"""
    rows = get_all_questions(user_id, category)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', '科目', '题目', '选项A', '选项B', '选项C', '选项D', '答案', '答对', '答错', '创建时间'])
    for r in rows:
        opts = json.loads(r[4]) if r[4] else {}
        writer.writerow([r[0], r[2], r[3], opts.get('A',''), opts.get('B',''),
                         opts.get('C',''), opts.get('D',''), r[5], r[6], r[7], r[8]])
    return output.getvalue()


# ==================== 统计操作 ====================
def get_category_stats(user_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT q.category, COUNT(ar.id), SUM(ar.is_correct)
            FROM answer_records ar JOIN questions q ON ar.question_id=q.id
            WHERE ar.user_id=? GROUP BY q.category
        ''', (user_id,))
        results = cursor.fetchall()
        conn.close()
        stats = {}
        for cat, total, correct in results:
            stats[cat] = {'total': total, 'accuracy': round((correct/total*100),1) if total else 0}
        return stats
    except Exception:
        return {}


def get_daily_stats(user_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DATE(answered_at), COUNT(*), SUM(is_correct)
            FROM answer_records WHERE user_id=? AND answered_at >= date('now','-7 days')
            GROUP BY DATE(answered_at) ORDER BY DATE(answered_at)
        ''', (user_id,))
        results = cursor.fetchall()
        conn.close()
        if not results: return pd.DataFrame()
        df = pd.DataFrame(results, columns=['日期','答题数','正确数'])
        df['正确率(%)'] = (df['正确数']/df['答题数']*100).round(1)
        return df
    except Exception:
        return pd.DataFrame()


# ==================== 笔记操作 ====================
def save_note_summary(user_id, filename, summary):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO note_summaries (user_id,filename,summary) VALUES (?,?,?)',
                       (user_id, filename, summary))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(str(e))


def get_note_summaries(user_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT id,filename,summary,created_at FROM note_summaries WHERE user_id=? ORDER BY created_at DESC',
                       (user_id,))
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception:
        return []


def delete_note(user_id, note_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM note_summaries WHERE id=? AND user_id=?', (note_id, user_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ==================== 任务进度操作 ====================
def save_task_chunk(user_id, task_id, task_type, chunk_index, total_chunks, result):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO task_chunks (user_id,task_id,task_type,chunk_index,total_chunks,result) VALUES (?,?,?,?,?,?)',
            (user_id, task_id, task_type, chunk_index, total_chunks, result)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_task_chunks(user_id, task_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT chunk_index,total_chunks,result FROM task_chunks WHERE user_id=? AND task_id=? ORDER BY chunk_index',
                       (user_id, task_id))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def clear_task(user_id, task_id):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM task_chunks WHERE user_id=? AND task_id=?', (user_id, task_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ==================== LLM 调用 ====================
def call_llm_single(args):
    i, chunk, api_key, base_url, model_name, prompt_type = args
    if prompt_type == "summary":
        prompt = f"""请对以下学习资料进行结构化总结，使用 Markdown 格式输出，包含：
1. **核心考点**：关键知识点（3-5条）
2. **重点记忆项**：需要背诵的内容（3-5条）
3. **易混淆点**：容易出错的地方（2-3条）
要求简洁明了，突出重点。

资料内容：
{chunk}"""
    else:
        prompt = f"""从以下文本中提取所有选择题，严格按JSON数组格式输出，不要任何额外文字。
格式：[{{"content":"题目","A":"选项A","B":"选项B","C":"选项C","D":"选项D","answer":"A"}}]
只提取有完整题目+四选项+答案的题目。

文本：
{chunk}"""
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


def run_parallel_llm(chunks, api_key, base_url, model_name, prompt_type="summary",
                     user_id=None, task_id=None, on_chunk_done=None):
    """并行调用 LLM，实时进度条，每块完成立即写库"""
    total = len(chunks)
    results = [None] * total
    completed_count = [0]

    # 进度显示区域
    progress_col1, progress_col2 = st.columns([3, 1])
    with progress_col1:
        progress_bar = st.progress(0)
    with progress_col2:
        progress_text = st.empty()

    status_text = st.empty()
    placeholders = [st.empty() for _ in range(total)]

    args_list = [(i, chunk, api_key, base_url, model_name, prompt_type) for i, chunk in enumerate(chunks)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(call_llm_single, args): args[0] for args in args_list}
        for future in concurrent.futures.as_completed(futures):
            i, result = future.result()
            completed_count[0] += 1
            results[i] = result
            pct = completed_count[0] / total
            progress_bar.progress(pct)
            progress_text.markdown(f"**{int(pct*100)}%**")
            status_text.caption(f"⚡ 已完成 {completed_count[0]} / {total} 块")

            if result:
                if user_id and task_id:
                    save_task_chunk(user_id, task_id, prompt_type, i, total, result)
                if prompt_type == "summary":
                    placeholders[i].markdown(f"### 第 {i+1} 部分\n\n{result}\n\n---")
                if on_chunk_done:
                    on_chunk_done(i, result)

    progress_bar.progress(1.0)
    progress_text.markdown("**100%** ✅")
    status_text.empty()
    return results, placeholders


# ==================== 文档处理 ====================
def extract_text_from_pdf(file):
    try:
        text = ""
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: text += t + "\n"
        return text.strip() or None
    except Exception as e:
        st.error(f"PDF 解析失败：{e}")
        return None


def extract_text_from_docx(file):
    try:
        doc = Document(file)
        text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return text.strip() or None
    except Exception as e:
        st.error(f"Word 解析失败：{e}")
        return None


def chunk_text(text, max_length=15000):
    chunks = []
    while len(text) > max_length:
        chunks.append(text[:max_length])
        text = text[max_length:]
    if text: chunks.append(text)
    return chunks


# ==================== 登录/注册页面 ====================
def page_auth():
    st.markdown("""
    <div style="text-align:center; padding: 3rem 0 1rem 0;">
        <div style="font-size:4rem;">📚</div>
        <h1 style="background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-size:2.5rem;">AI 自学全能助手</h1>
        <p style="color:#888; font-size:1rem;">智能笔记 · 题库管理 · 学习追踪</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        tab_login, tab_register = st.tabs(["🔑 登录", "📝 注册"])

        with tab_login:
            username = st.text_input("用户名", key="login_u", placeholder="请输入用户名")
            password = st.text_input("密码", type="password", key="login_p", placeholder="请输入密码")
            if st.button("登 录", type="primary", use_container_width=True):
                if not username or not password:
                    st.error("请填写用户名和密码")
                else:
                    user, msg = login_user(username, password)
                    if user:
                        st.session_state.user = user
                        st.session_state.api_key = user['api_key']
                        st.session_state.base_url = user['base_url']
                        st.session_state.model_name = user['model_name']
                        st.session_state.app_name = user['app_name']
                        st.session_state.subjects = user['subjects']
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

        with tab_register:
            new_u = st.text_input("用户名（至少2字符）", key="reg_u")
            new_p = st.text_input("密码（至少6位）", type="password", key="reg_p")
            confirm_p = st.text_input("确认密码", type="password", key="reg_c")
            if st.button("注 册", type="primary", use_container_width=True):
                if not new_u or not new_p:
                    st.error("请填写完整")
                elif new_p != confirm_p:
                    st.error("两次密码不一致")
                else:
                    ok, msg = register_user(new_u, new_p)
                    if ok:
                        st.success(f"✅ {msg} 请切换到登录标签页")
                    else:
                        st.error(f"❌ {msg}")


# ==================== 侧边栏 ====================
def render_sidebar():
    user = st.session_state.user
    app_name = st.session_state.get('app_name') or DEFAULT_APP_NAME

    st.sidebar.markdown(f"""
    <div style="padding:1rem 0.5rem 0.5rem 0.5rem; text-align:center;">
        <div style="font-size:2rem;">📚</div>
        <div style="font-weight:700; font-size:1rem; color:#fff;">{app_name}</div>
        <div style="font-size:0.75rem; color:#aaa; margin-top:0.3rem;">👤 {user['username']}</div>
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.markdown("---")

    # API 配置
    with st.sidebar.expander("⚙️ API 配置", expanded=not st.session_state.get('api_key')):
        api_key = st.text_input("API Key", value=st.session_state.get('api_key',''), type="password")
        base_url = st.text_input("Base URL", value=st.session_state.get('base_url', DEFAULT_BASE_URL))
        model_name = st.text_input("Model Name", value=st.session_state.get('model_name', DEFAULT_MODEL_NAME))
        if st.button("💾 保存配置", type="primary"):
            st.session_state.api_key = api_key
            st.session_state.base_url = base_url
            st.session_state.model_name = model_name
            subjects = st.session_state.get('subjects', DEFAULT_SUBJECTS)
            app_name_val = st.session_state.get('app_name', DEFAULT_APP_NAME)
            save_user_settings(user['id'], api_key, base_url, model_name, app_name_val, subjects)
            st.success("✅ 已保存")
            st.rerun()

    st.sidebar.markdown("---")

    # 题库概况
    total_q = get_question_count(user['id'])
    st.sidebar.markdown(f"""
    <div style="background:rgba(255,255,255,0.05);border-radius:8px;padding:0.8rem;margin-bottom:0.5rem;">
        <div style="font-size:1.5rem;font-weight:700;color:#667eea;">{total_q}</div>
        <div style="font-size:0.75rem;color:#aaa;">题库总量</div>
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.markdown("---")

    page = st.sidebar.radio("📌 导航", [
        "🏠 首页总览",
        "📚 智能笔记总结",
        "🔍 PDF 题目提取",
        "✍️ 智能刷题",
        "📋 题库管理",
        "📊 学习看板",
        "📅 学习计划",
        "⚙️ 个人设置"
    ])

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 退出登录", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    return page


# ==================== 首页总览 ====================
def page_home():
    user = st.session_state.user
    app_name = st.session_state.get('app_name') or DEFAULT_APP_NAME
    page_header(f"欢迎回来，{user['username']} 👋", app_name)

    # 统计卡片
    total_q = get_question_count(user['id'])
    notes = get_note_summaries(user['id'])
    daily = get_daily_stats(user['id'])
    total_answered = int(daily['答题数'].sum()) if not daily.empty else 0
    today_answered = int(daily[daily['日期'] == str(datetime.now().date())]['答题数'].sum()) if not daily.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    stat_card(total_q, "📦 题库总量", c1)
    stat_card(len(notes), "📄 笔记总数", c2)
    stat_card(total_answered, "✍️ 累计答题", c3)
    stat_card(today_answered, "🔥 今日答题", c4)

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📈 近7天答题趋势")
        if not daily.empty:
            st.line_chart(daily.set_index('日期')['答题数'])
        else:
            st.info("暂无数据，去刷题吧！")

    with col2:
        st.subheader("🎯 各科正确率")
        stats = get_category_stats(user['id'])
        if stats:
            df = pd.DataFrame([{"科目": k, "正确率(%)": v['accuracy']} for k, v in stats.items()])
            st.bar_chart(df.set_index('科目')['正确率(%)'])
        else:
            st.info("暂无数据，去刷题吧！")

    if not st.session_state.get('api_key'):
        st.warning("⚠️ 还未配置 API Key，请点击左侧「⚙️ API 配置」填写后才能使用 AI 功能")


# ==================== 智能笔记总结 ====================
def page_note_summary():
    page_header("📚 智能笔记总结", "上传文档，AI 自动提取核心知识点，永久保存")
    user = st.session_state.user
    api_key = st.session_state.get('api_key', '')

    if not api_key:
        st.warning("⚠️ 请先在左侧配置 API Key")
        return

    tab_new, tab_history = st.tabs(["📤 新建总结", "📂 历史记录"])

    with tab_new:
        uploaded_file = st.file_uploader("选择文件（支持 PDF / DOCX）", type=['pdf', 'docx'])
        if not uploaded_file:
            st.info("📎 请上传文件后点击「开始总结」")
            return

        file_type = uploaded_file.name.split('.')[-1].lower()
        if st.button("🚀 开始总结", type="primary"):
            with st.spinner("解析文档中..."):
                text = extract_text_from_pdf(uploaded_file) if file_type == 'pdf' \
                    else extract_text_from_docx(uploaded_file)

            if not text:
                st.error("文档解析失败，请确认文件含有可选中的文字")
                return

            chunks = chunk_text(text)
            import hashlib as _hl
            task_id = _hl.md5(f"{user['id']}_{uploaded_file.name}_{len(text)}".encode()).hexdigest()[:12]
            clear_task(user['id'], task_id)

            st.success(f"✅ 解析成功，共 {len(text):,} 字，分 {len(chunks)} 块并行处理")
            st.markdown("---")

            results, _ = run_parallel_llm(
                chunks, api_key,
                st.session_state.get('base_url', DEFAULT_BASE_URL),
                st.session_state.get('model_name', DEFAULT_MODEL_NAME),
                prompt_type="summary",
                user_id=user['id'], task_id=task_id
            )

            all_chunks = get_task_chunks(user['id'], task_id)
            if all_chunks:
                ordered = sorted(all_chunks, key=lambda x: x[0])
                full = "\n\n---\n\n".join([f"### 第{r[0]+1}部分\n\n{r[2]}" for r in ordered if r[2]])
                save_note_summary(user['id'], uploaded_file.name, full)
                clear_task(user['id'], task_id)
                st.markdown("""<div class="success-banner">🎉 总结完成，已永久保存！</div>""", unsafe_allow_html=True)
            else:
                st.error("总结失败，请检查 API 配置")

    with tab_history:
        summaries = get_note_summaries(user['id'])
        if not summaries:
            st.info("暂无历史总结")
            return
        for sid, filename, summary, created_at in summaries:
            with st.expander(f"📄 {filename} · {created_at[:16]}"):
                col1, col2 = st.columns([5, 1])
                with col2:
                    if st.button("🗑️ 删除", key=f"del_note_{sid}"):
                        delete_note(user['id'], sid)
                        st.rerun()
                st.markdown(summary)


# ==================== PDF 题目提取 ====================
def page_extract_questions():
    page_header("🔍 PDF 题目提取", "上传题目 PDF，AI 自动识别并实时导入题库")
    user = st.session_state.user
    api_key = st.session_state.get('api_key', '')
    subjects = st.session_state.get('subjects', DEFAULT_SUBJECTS)

    if not api_key:
        st.warning("⚠️ 请先在左侧配置 API Key")
        return

    col1, col2 = st.columns(2)
    with col1:
        uploaded_file = st.file_uploader("选择题目 PDF", type=['pdf'])
    with col2:
        category = st.selectbox("题目科目", subjects)
        st.metric("我的题库", f"{get_question_count(user['id'])} 道")

    if not uploaded_file:
        st.info("📎 请上传含题目的 PDF")
        return

    if st.button("🚀 开始提取", type="primary"):
        with st.spinner("解析 PDF..."):
            text = extract_text_from_pdf(uploaded_file)

        if not text:
            st.error("PDF 解析失败")
            return

        chunks = chunk_text(text, max_length=6000)
        st.info(f"📦 分 {len(chunks)} 块并行提取，题目实时导入题库...")

        realtime_success = [0]
        realtime_fail = [0]
        import_log = st.empty()

        def on_extract_done(i, raw):
            try:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("```")[1]
                    if cleaned.startswith("json"): cleaned = cleaned[4:]
                qs = json.loads(cleaned.strip())
                if not isinstance(qs, list): return
                for q in qs:
                    opts = {"A": q.get("A",""), "B": q.get("B",""),
                            "C": q.get("C",""), "D": q.get("D","")}
                    if q.get("content") and all(opts.values()) and q.get("answer") in ["A","B","C","D"]:
                        if add_question(user['id'], category, q["content"], opts, q["answer"]):
                            realtime_success[0] += 1
                        else:
                            realtime_fail[0] += 1
                    else:
                        realtime_fail[0] += 1
                import_log.caption(f"📥 已实时导入 {realtime_success[0]} 道题...")
            except Exception:
                pass

        import hashlib as _hl
        task_id = _hl.md5(f"{user['id']}_extract_{uploaded_file.name}".encode()).hexdigest()[:12]

        run_parallel_llm(
            chunks, api_key,
            st.session_state.get('base_url', DEFAULT_BASE_URL),
            st.session_state.get('model_name', DEFAULT_MODEL_NAME),
            prompt_type="extract",
            user_id=user['id'], task_id=task_id,
            on_chunk_done=on_extract_done
        )

        import_log.empty()
        clear_task(user['id'], task_id)

        if realtime_success[0] == 0:
            st.error("未提取到题目，请确认 PDF 含完整的四选一题目及答案")
        else:
            st.markdown(f"""<div class="success-banner">
                🎉 提取完成！成功导入 {realtime_success[0]} 道题
                {'，跳过 '+str(realtime_fail[0])+' 道格式不完整的' if realtime_fail[0] else ''}
            </div>""", unsafe_allow_html=True)
            st.balloons()


# ==================== 智能刷题 ====================
def page_practice():
    page_header("✍️ 智能刷题", "错题权重更高，薄弱知识点优先推送")
    user = st.session_state.user
    subjects = st.session_state.get('subjects', DEFAULT_SUBJECTS)
    CATEGORIES = ["全部"] + subjects

    with st.expander("➕ 手动添加题目"):
        with st.form("add_q", clear_on_submit=True):
            cat = st.selectbox("科目", subjects)
            content = st.text_area("题目内容", placeholder="输入题目正文...")
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

    col1, col2 = st.columns([2, 1])
    with col1:
        selected_cat = st.selectbox("按科目筛选", CATEGORIES)
    with col2:
        count = get_question_count(user['id'], selected_cat)
        st.metric("题目数量", f"{count} 道")

    if count == 0:
        st.info("📭 暂无题目，请先通过「PDF 题目提取」或「手动添加」录入")
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

    st.markdown(f"""<div class="question-card">
        <div style="color:#667eea;font-size:0.85rem;font-weight:600;margin-bottom:0.8rem;">【{question['category']}】</div>
        <div style="font-size:1.05rem;line-height:1.8;">{question['content']}</div>
    </div>""", unsafe_allow_html=True)

    for k, v in question['options'].items():
        st.markdown(f"**{k}.** {v}")
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
            st.markdown("""<div class="success-banner">✅ 回答正确！</div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div class="error-banner">❌ 回答错误！正确答案是 <b>{question['answer']}</b></div>""", unsafe_allow_html=True)

        correct_count, wrong_count = get_question_latest_stats(question['id'])
        st.caption(f"📊 本题累计：答对 {correct_count} 次 / 答错 {wrong_count} 次")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("➡️ 下一题", type="primary"):
                st.session_state.current_question = get_weighted_question(user['id'], selected_cat)
                st.session_state.user_answer = None
                st.session_state.answered = False
                st.rerun()
        with col2:
            if st.button("🗑️ 删除本题"):
                delete_question(user['id'], question['id'])
                st.session_state.current_question = None
                st.session_state.answered = False
                st.rerun()


# ==================== 题库管理 ====================
def page_question_bank():
    page_header("📋 题库管理", "查看、导出、删除你的题目")
    user = st.session_state.user
    subjects = st.session_state.get('subjects', DEFAULT_SUBJECTS)
    CATEGORIES = ["全部"] + subjects

    col1, col2, col3 = st.columns(3)
    with col1:
        filter_cat = st.selectbox("科目筛选", CATEGORIES)
    with col2:
        total = get_question_count(user['id'], filter_cat)
        st.metric("筛选结果", f"{total} 道")
    with col3:
        # 导出按钮
        if total > 0:
            csv_data = export_questions_csv(user['id'], filter_cat)
            st.download_button(
                label="📥 导出 CSV",
                data=csv_data.encode('utf-8-sig'),
                file_name=f"题库_{filter_cat}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                type="primary"
            )

    st.markdown("---")

    # 危险操作：清空题库
    with st.expander("⚠️ 危险操作"):
        st.warning("以下操作不可撤销！")
        if st.button(f"🗑️ 清空「{filter_cat}」全部题目", type="secondary"):
            if 'confirm_delete' not in st.session_state:
                st.session_state.confirm_delete = True
                st.rerun()

        if st.session_state.get('confirm_delete'):
            st.error("确认要删除吗？此操作不可撤销！")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ 确认删除", type="primary"):
                    delete_all_questions(user['id'], filter_cat)
                    st.session_state.pop('confirm_delete', None)
                    st.success("已清空")
                    st.rerun()
            with c2:
                if st.button("❌ 取消"):
                    st.session_state.pop('confirm_delete', None)
                    st.rerun()

    # 题目列表
    questions = get_all_questions(user['id'], filter_cat)
    if not questions:
        st.info("暂无题目")
        return

    for q in questions:
        opts = json.loads(q[4]) if q[4] else {}
        with st.expander(f"【{q[2]}】{q[3][:50]}...  ✅{q[6]} ❌{q[7]}"):
            st.write(f"**题目：** {q[3]}")
            for opt in ['A','B','C','D']:
                marker = "✅ " if opt == q[5] else ""
                st.write(f"{marker}**{opt}.** {opts.get(opt,'')}")
            st.caption(f"答案：{q[5]} · 答对{q[6]}次 · 答错{q[7]}次 · 添加于{q[8][:10]}")
            if st.button("🗑️ 删除此题", key=f"del_q_{q[0]}"):
                delete_question(user['id'], q[0])
                st.rerun()


# ==================== 学习看板 ====================
def page_dashboard():
    page_header("📊 学习看板", "可视化你的学习进度与正确率")
    user = st.session_state.user

    stats = get_category_stats(user['id'])
    daily = get_daily_stats(user['id'])

    # 顶部统计
    total_answered = int(daily['答题数'].sum()) if not daily.empty else 0
    avg_accuracy = round(daily['正确率(%)'].mean(), 1) if not daily.empty else 0
    best_day = int(daily['答题数'].max()) if not daily.empty else 0

    c1, c2, c3 = st.columns(3)
    stat_card(f"{total_answered}", "近7天总答题", c1)
    stat_card(f"{avg_accuracy}%", "近7天平均正确率", c2)
    stat_card(f"{best_day}", "单日最高答题", c3)

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("各科目正确率")
        if stats:
            df = pd.DataFrame([{"科目": k, "答题数": v['total'], "正确率(%)": v['accuracy']} for k, v in stats.items()])
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.bar_chart(df.set_index('科目')['正确率(%)'])
        else:
            st.info("暂无数据")

    with col2:
        st.subheader("近7天答题趋势")
        if not daily.empty:
            st.dataframe(daily[['日期','答题数','正确率(%)']], hide_index=True, use_container_width=True)
            st.line_chart(daily.set_index('日期')[['答题数','正确率(%)']])
        else:
            st.info("暂无近7天数据")


# ==================== 学习计划 ====================
def page_study_plan():
    page_header("📅 学习计划", "根据考试日期动态生成每日任务")

    target_date = st.date_input("设置目标日期", value=datetime.now().date() + timedelta(days=90),
                                min_value=datetime.now().date())
    days_left = (target_date - datetime.now().date()).days

    if days_left > 60:
        stage, badge, color = "基础期", "🟢", "info"
        tasks = ["📖 基础练习10题（重理解）", "📝 上传笔记生成AI总结",
                 "💡 整理1个知识模块", "🔍 复习昨日错题"]
        advice = "**基础期：** 建立知识框架，不追求速度，先求理解。"
    elif days_left >= 30:
        stage, badge, color = "强化期", "🟡", "warning"
        tasks = ["📖 混合练习20题", "📝 复习错题集攻克薄弱点",
                 "💡 限时模拟1套", "🔍 总结答题规律"]
        advice = "**强化期：** 大量刷题提速，限时训练，集中突破薄弱点。"
    elif days_left > 0:
        stage, badge, color = "冲刺期", "🔴", "error"
        tasks = ["📖 全真模拟30题（严格限时）", "📝 攻克错误率最高科目",
                 "💡 完整模拟2套", "🔍 回顾高频考点", "🎯 调整作息保持状态"]
        advice = "**冲刺期：** 全真模拟、查漏补缺、减少新知识输入。"
    else:
        st.error("目标日期已过，请重新设置")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("⏳ 剩余天数", f"{days_left} 天")
    c2.metric("📌 当前阶段", f"{badge} {stage}")
    c3.metric("🗓 目标日期", target_date.strftime("%Y-%m-%d"))

    # 进度条
    elapsed = max(0, 90 - days_left)
    st.progress(min(elapsed/90, 1.0), text=f"备考进度 {min(int(elapsed/90*100),100)}%（以90天为基准）")

    st.markdown("---")
    st.subheader("📋 今日任务")
    for i, task in enumerate(tasks):
        st.checkbox(task, key=f"task_{i}")

    st.markdown("---")
    getattr(st, color)(advice)


# ==================== 个人设置 ====================
def page_settings():
    page_header("⚙️ 个人设置", "自定义应用名称、科目，修改密码")
    user = st.session_state.user

    tab1, tab2, tab3 = st.tabs(["🎨 应用个性化", "🔒 修改密码", "📊 我的数据"])

    with tab1:
        st.subheader("自定义应用名称")
        app_name = st.text_input("应用名称", value=st.session_state.get('app_name', DEFAULT_APP_NAME),
                                 placeholder="如：考研助手、英语学习...")

        st.subheader("自定义科目分类")
        subjects_str = st.text_area(
            "科目列表（每行一个）",
            value="\n".join(st.session_state.get('subjects', DEFAULT_SUBJECTS)),
            placeholder="语文\n数学\n英语\n..."
        )
        st.caption("修改科目后，新添加的题目将使用新科目，已有题目科目不变")

        if st.button("💾 保存设置", type="primary"):
            subjects = [s.strip() for s in subjects_str.strip().split('\n') if s.strip()]
            if not subjects:
                st.error("至少需要一个科目")
            else:
                ok = save_user_settings(
                    user['id'],
                    st.session_state.get('api_key', ''),
                    st.session_state.get('base_url', DEFAULT_BASE_URL),
                    st.session_state.get('model_name', DEFAULT_MODEL_NAME),
                    app_name, subjects
                )
                if ok:
                    st.session_state.app_name = app_name
                    st.session_state.subjects = subjects
                    st.success("✅ 保存成功！刷新页面生效")
                    st.rerun()

    with tab2:
        st.subheader("修改密码")
        with st.form("change_pwd"):
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
                        for k in list(st.session_state.keys()):
                            del st.session_state[k]
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

    with tab3:
        st.subheader("我的数据统计")
        c1, c2, c3 = st.columns(3)
        stat_card(get_question_count(user['id']), "题库题目", c1)
        stat_card(len(get_note_summaries(user['id'])), "历史笔记", c2)
        daily = get_daily_stats(user['id'])
        stat_card(int(daily['答题数'].sum()) if not daily.empty else 0, "近7天答题", c3)


# ==================== 主程序 ====================
def main():
    st.set_page_config(
        page_title="AI 自学全能助手",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    inject_css()
    init_database()

    if 'user' not in st.session_state:
        page_auth()
        return

    for key, default in [
        ('summary_results', []), ('current_question', None),
        ('answered', False), ('user_answer', None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    page = render_sidebar()

    pages = {
        "🏠 首页总览": page_home,
        "📚 智能笔记总结": page_note_summary,
        "🔍 PDF 题目提取": page_extract_questions,
        "✍️ 智能刷题": page_practice,
        "📋 题库管理": page_question_bank,
        "📊 学习看板": page_dashboard,
        "📅 学习计划": page_study_plan,
        "⚙️ 个人设置": page_settings,
    }
    pages.get(page, page_home)()


if __name__ == "__main__":
    main()

# ==================== 依赖清单 ====================
# pip install streamlit pdfplumber python-docx openai pandas
