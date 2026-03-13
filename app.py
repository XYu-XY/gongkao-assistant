"""
公考自学全能助手 - 桌面端 Web 应用
技术栈：Streamlit + SQLite + OpenAI API

合并版本优化点：
1. API Key 通过侧边栏安全输入，session_state 持久保存，已填时自动收起
2. 总结结果逐块实时显示，session_state 防刷新丢失
3. 刷题支持按科目筛选，题库数量实时显示
4. 答题后从数据库重新拉取最新统计，数据准确
5. 备考进度条可视化
6. 所有边界情况友好提示
"""

import streamlit as st
import sqlite3
import json
import random
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
from docx import Document

# ==================== 默认配置项 ====================
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL_NAME = "gpt-4o-mini"
DB_PATH = "gongkao.db"
# ====================================================


# ==================== 数据库初始化 ====================
def init_database():
    """初始化 SQLite 数据库，创建题目表和答题记录表"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            options TEXT NOT NULL,
            answer TEXT NOT NULL,
            correct_count INTEGER DEFAULT 0,
            wrong_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS answer_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            is_correct INTEGER NOT NULL,
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id)
        )
    ''')

    conn.commit()
    conn.close()


def get_question_count(category=None):
    """获取题库题目总数，可按科目筛选"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if category and category != "全部":
            cursor.execute('SELECT COUNT(*) FROM questions WHERE category = ?', (category,))
        else:
            cursor.execute('SELECT COUNT(*) FROM questions')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def add_question(category, content, options, answer):
    """添加新题目到数据库"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        options_json = json.dumps(options, ensure_ascii=False)
        cursor.execute(
            'INSERT INTO questions (category, content, options, answer) VALUES (?, ?, ?, ?)',
            (category, content, options_json, answer)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"添加题目失败：{str(e)}")
        return False


def get_weighted_question(category=None):
    """
    按权重抽取一道题目。
    weight = (wrong_count * 2) + 1，错题被抽中概率更高。
    支持按科目筛选。
    """
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if category and category != "全部":
            cursor.execute('SELECT * FROM questions WHERE category = ?', (category,))
        else:
            cursor.execute('SELECT * FROM questions')
        questions = cursor.fetchall()
        conn.close()

        if not questions:
            return None

        weights = [(q[6] * 2) + 1 for q in questions]
        selected = random.choices(questions, weights=weights, k=1)[0]

        return {
            'id': selected[0],
            'category': selected[1],
            'content': selected[2],
            'options': json.loads(selected[3]),
            'answer': selected[4],
            'correct_count': selected[5],
            'wrong_count': selected[6]
        }
    except Exception as e:
        st.error(f"抽题失败：{str(e)}")
        return None


def update_question_stats(question_id, is_correct):
    """更新题目答对/答错计数，并写入答题记录"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        if is_correct:
            cursor.execute('UPDATE questions SET correct_count = correct_count + 1 WHERE id = ?', (question_id,))
        else:
            cursor.execute('UPDATE questions SET wrong_count = wrong_count + 1 WHERE id = ?', (question_id,))
        cursor.execute(
            'INSERT INTO answer_records (question_id, is_correct) VALUES (?, ?)',
            (question_id, 1 if is_correct else 0)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"更新统计失败：{str(e)}")
        return False


def get_question_latest_stats(question_id):
    """从数据库重新获取题目最新统计数据，确保数据准确"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT correct_count, wrong_count FROM questions WHERE id = ?', (question_id,))
        result = cursor.fetchone()
        conn.close()
        return result if result else (0, 0)
    except Exception:
        return (0, 0)


def get_category_stats():
    """获取各科目答题正确率统计"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT q.category,
                   COUNT(ar.id) as total,
                   SUM(ar.is_correct) as correct
            FROM answer_records ar
            JOIN questions q ON ar.question_id = q.id
            GROUP BY q.category
        ''')
        results = cursor.fetchall()
        conn.close()

        stats = {}
        for row in results:
            category, total, correct = row
            accuracy = round((correct / total * 100), 1) if total > 0 else 0
            stats[category] = {'total': total, 'accuracy': accuracy}
        return stats
    except Exception as e:
        st.error(f"获取统计失败：{str(e)}")
        return {}


def get_daily_stats():
    """获取最近 7 天每日答题数与正确率"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DATE(answered_at) as date,
                   COUNT(*) as total,
                   SUM(is_correct) as correct
            FROM answer_records
            WHERE answered_at >= date('now', '-7 days')
            GROUP BY DATE(answered_at)
            ORDER BY date
        ''')
        results = cursor.fetchall()
        conn.close()

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results, columns=['日期', '答题数', '正确数'])
        df['正确率(%)'] = (df['正确数'] / df['答题数'] * 100).round(1)
        return df
    except Exception as e:
        st.error(f"获取每日统计失败：{str(e)}")
        return pd.DataFrame()


# ==================== LLM 调用 ====================
def call_llm(prompt, api_key, base_url, model_name):
    """
    调用 OpenAI 兼容接口。
    延迟导入 openai，避免未安装时整体崩溃。
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return response.choices[0].message.content
    except ImportError:
        st.error("请先安装 openai 库：pip install openai")
        return None
    except Exception as e:
        st.error(f"LLM 调用失败：{str(e)}")
        return None


# ==================== 文档处理 ====================
def extract_text_from_pdf(file):
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


def extract_text_from_docx(file):
    """从 Word 文档提取全文"""
    try:
        doc = Document(file)
        text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return text.strip() or None
    except Exception as e:
        st.error(f"Word 解析失败：{str(e)}")
        return None


def chunk_text(text, max_length=15000):
    """将文本按最大长度分块，默认15000字减少分块数量"""
    chunks = []
    while len(text) > max_length:
        chunks.append(text[:max_length])
        text = text[max_length:]
    if text:
        chunks.append(text)
    return chunks


def call_llm_single(args):
    """
    单块 LLM 调用，用于并行执行。
    接收 tuple: (index, chunk, api_key, base_url, model_name)
    返回 (index, result) 保证顺序。
    """
    i, chunk, api_key, base_url, model_name = args
    prompt = f"""请对以下公考学习资料进行结构化总结，使用 Markdown 格式输出，包含：
1. **核心考点**：列出本段的关键知识点（3-5 条）
2. **重点记忆项**：需要重点背诵的内容（3-5 条）
3. **易混淆点**：容易出错或混淆的地方（2-3 条）

要求：简洁明了，突出重点，避免冗余。

资料内容：
{chunk}
"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return (i, response.choices[0].message.content)
    except Exception as e:
        return (i, None)


def summarize_document(text, api_key, base_url, model_name):
    """
    并行调用 LLM 生成结构化 Markdown 总结。
    最多同时发起 5 个请求，速度比串行快 3-5 倍。
    结果按原始顺序排列后展示。
    """
    import concurrent.futures

    if not text:
        st.error("文档内容为空")
        return None

    chunks = chunk_text(text)
    total = len(chunks)
    st.session_state.summary_results = [None] * total  # 预分配，保证顺序

    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text(f"⚡ 并行处理中，共 {total} 块，同时发起最多 5 个请求...")

    # 预先创建占位符，按顺序显示结果
    placeholders = []
    result_container = st.container()
    with result_container:
        for i in range(total):
            placeholders.append(st.empty())

    completed = 0
    args_list = [(i, chunk, api_key, base_url, model_name) for i, chunk in enumerate(chunks)]

    # 并行最多5个线程，避免触发API限流
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(call_llm_single, args): args[0] for args in args_list}

        for future in concurrent.futures.as_completed(futures):
            i, summary = future.result()
            completed += 1
            progress_bar.progress(completed / total)

            if summary:
                block = f"### 第 {i+1} 部分总结\n\n{summary}"
                st.session_state.summary_results[i] = block
                # 在对应位置的占位符中渲染（保证顺序）
                placeholders[i].markdown(block + "\n\n---")
                status_text.text(f"⚡ 已完成 {completed} / {total} 块...")
            else:
                placeholders[i].warning(f"第 {i+1} 块处理失败，已跳过")

    progress_bar.empty()
    status_text.empty()

    # 过滤掉失败的块
    final_results = [r for r in st.session_state.summary_results if r]
    st.session_state.summary_results = final_results

    if not final_results:
        st.error("所有块处理失败，请检查 API 配置是否正确")
        return None

    return "\n\n---\n\n".join(final_results)


# ==================== 页面：智能笔记总结 ====================
def page_note_summary():
    """智能笔记总结页面"""
    st.header("📚 智能笔记总结")
    st.caption("上传 PDF 或 Word 文档，AI 自动提取核心考点、重点记忆项和易混淆点")

    if not st.session_state.get('api_key'):
        st.warning("⚠️ 请先在左侧侧边栏填写并保存 API Key")
        return

    uploaded_file = st.file_uploader("选择文件（支持 PDF / DOCX）", type=['pdf', 'docx'])

    if not uploaded_file:
        # 没有上传文件时，展示上次的总结结果（防刷新丢失）
        if st.session_state.get('summary_results'):
            st.info("📄 以下是上次的总结结果（重新上传文件可覆盖）：")
            for block in st.session_state.summary_results:
                st.markdown(block)
                st.markdown("---")
        else:
            st.info("请上传文件后点击「开始总结」")
        return

    file_type = uploaded_file.name.split('.')[-1].lower()

    if st.button("🚀 开始总结", type="primary"):
        with st.spinner("正在解析文档..."):
            text = extract_text_from_pdf(uploaded_file) if file_type == 'pdf' \
                else extract_text_from_docx(uploaded_file)

        if not text:
            st.error("文档内容为空或解析失败（扫描件图片版无法识别，请使用含文字的 PDF）")
            return

        total_chunks = len(chunk_text(text))
        st.success(f"✅ 文档解析成功，共 {len(text)} 字，将分 {total_chunks} 块处理")
        st.markdown("---")
        st.subheader("📝 总结结果（逐块生成，完成一块显示一块）")

        summary = summarize_document(
            text,
            st.session_state.api_key,
            st.session_state.base_url,
            st.session_state.model_name
        )
        if summary:
            st.success("🎉 全部总结完成！")

    # 按钮未点击但有历史结果时继续展示
    elif st.session_state.get('summary_results'):
        st.subheader("📝 总结结果")
        for block in st.session_state.summary_results:
            st.markdown(block)
            st.markdown("---")


# ==================== 页面：智能刷题系统 ====================
def page_practice():
    """权重错题刷题页面，支持按科目筛选"""
    st.header("✍️ 智能刷题系统")

    CATEGORIES = ["全部", "言语理解", "判断推理", "资料分析", "数量关系", "常识判断"]

    # ---- 添加题目 ----
    with st.expander("➕ 手动添加题目"):
        with st.form("add_question_form", clear_on_submit=True):
            category = st.selectbox("科目分类", CATEGORIES[1:])
            content = st.text_area("题目内容", placeholder="请输入题目正文...")

            col1, col2 = st.columns(2)
            with col1:
                option_a = st.text_input("选项 A")
                option_b = st.text_input("选项 B")
            with col2:
                option_c = st.text_input("选项 C")
                option_d = st.text_input("选项 D")

            answer = st.selectbox("正确答案", ["A", "B", "C", "D"])
            submitted = st.form_submit_button("✅ 添加题目", type="primary")

            if submitted:
                if all([content, option_a, option_b, option_c, option_d]):
                    options = {"A": option_a, "B": option_b, "C": option_c, "D": option_d}
                    if add_question(category, content, options, answer):
                        st.success("题目添加成功！")
                        st.session_state.pop('current_question', None)
                        st.rerun()
                else:
                    st.error("请填写全部字段后再提交")

    st.markdown("---")

    # ---- 科目筛选 ----
    selected_category = st.selectbox("按科目筛选", CATEGORIES, key="filter_category")
    count = get_question_count(selected_category)
    st.caption(f"当前筛选范围共 **{count}** 道题")

    if count == 0:
        st.info("📭 题库为空，请先在上方「手动添加题目」中录入题目")
        return

    # ---- 抽题 ----
    if st.button("🎲 抽取一道题", type="primary"):
        question = get_weighted_question(selected_category)
        if question:
            st.session_state.current_question = question
            st.session_state.user_answer = None
            st.session_state.answered = False
            st.rerun()
        else:
            st.warning("该科目暂无题目，请更换筛选条件或添加题目")

    # 初始化 session_state
    for key, default in [('current_question', None), ('answered', False), ('user_answer', None)]:
        if key not in st.session_state:
            st.session_state[key] = default

    question = st.session_state.current_question

    if not question:
        st.info("💡 点击「抽取一道题」开始练习，或先添加题目到题库")
        return

    # ---- 展示题目 ----
    st.subheader(f"【{question['category']}】")
    st.write(question['content'])
    st.write("")
    for key, value in question['options'].items():
        st.write(f"**{key}.** {value}")
    st.write("")

    if not st.session_state.answered:
        user_answer = st.radio("请选择答案", ["A", "B", "C", "D"],
                               key="answer_radio", horizontal=True)
        if st.button("📨 提交答案", type="primary"):
            is_correct = (user_answer == question['answer'])
            update_question_stats(question['id'], is_correct)
            st.session_state.user_answer = user_answer
            st.session_state.answered = True
            st.rerun()
    else:
        is_correct = (st.session_state.user_answer == question['answer'])
        if is_correct:
            st.success(f"✅ 回答正确！正确答案：**{question['answer']}**")
        else:
            st.error(f"❌ 回答错误！你的答案：**{st.session_state.user_answer}**，正确答案：**{question['answer']}**")

        # 从数据库重新拉取最新统计，数据准确
        correct_count, wrong_count = get_question_latest_stats(question['id'])
        st.info(f"📊 本题累计：答对 {correct_count} 次 / 答错 {wrong_count} 次")

        if st.button("➡️ 继续下一题"):
            st.session_state.current_question = get_weighted_question(selected_category)
            st.session_state.user_answer = None
            st.session_state.answered = False
            st.rerun()


# ==================== 页面：备考数据看板 ====================
def page_dashboard():
    """备考数据看板，展示各科正确率和近 7 天趋势"""
    st.header("📊 备考数据看板")

    # ---- 各科目统计 ----
    st.subheader("各科目答题统计")
    category_stats = get_category_stats()

    if category_stats:
        df_cat = pd.DataFrame([
            {"科目": k, "答题数": v['total'], "正确率(%)": v['accuracy']}
            for k, v in category_stats.items()
        ])
        col1, col2 = st.columns([1, 2])
        with col1:
            st.dataframe(df_cat, use_container_width=True, hide_index=True)
        with col2:
            st.bar_chart(df_cat.set_index('科目')['正确率(%)'])
    else:
        st.info("📝 暂无答题数据，去「✍️ 智能刷题系统」开始练习吧！")
        st.markdown("""
        **如何开始：**
        1. 前往「✍️ 智能刷题系统」页面
        2. 点击「➕ 手动添加题目」录入题目
        3. 开始答题后，这里会自动显示统计数据
        """)

    st.markdown("---")

    # ---- 最近 7 天趋势 ----
    st.subheader("最近 7 天答题趋势")
    daily_stats = get_daily_stats()

    if not daily_stats.empty:
        st.dataframe(daily_stats[['日期', '答题数', '正确率(%)']],
                     use_container_width=True, hide_index=True)
        col1, col2 = st.columns(2)
        with col1:
            st.caption("每日答题量")
            st.line_chart(daily_stats.set_index('日期')['答题数'])
        with col2:
            st.caption("每日正确率 (%)")
            st.line_chart(daily_stats.set_index('日期')['正确率(%)'])
    else:
        st.info("📈 最近 7 天暂无答题记录，坚持每天刷题，这里会展示你的进步曲线！")


# ==================== 页面：动态学习计划 ====================
def page_study_plan():
    """根据考试日期动态生成学习阶段和今日任务"""
    st.header("📅 动态学习计划")

    target_date = st.date_input(
        "设置考试目标日期",
        value=datetime.now().date() + timedelta(days=90),
        min_value=datetime.now().date()
    )

    today = datetime.now().date()
    days_left = (target_date - today).days

    # ---- 阶段判断 ----
    if days_left > 60:
        stage, badge, color = "基础期", "🟢", "info"
        tasks = [
            "📖 刷 10 道判断推理题（不计时，重在理解）",
            "📝 上传一份笔记用 AI 生成总结",
            "💡 整理常识判断知识点 1 个模块",
            "🔍 复习昨日错题（若有）"
        ]
        advice = "**基础期重点：** 系统建立知识框架，不追求速度，先求理解再求正确率。"
    elif 30 <= days_left <= 60:
        stage, badge, color = "强化期", "🟡", "warning"
        tasks = [
            "📖 刷 20 道混合题（言语+判断各 10 道）",
            "📝 复习错题集，重点攻克薄弱科目",
            "💡 做 1 套限时模拟卷（严格计时）",
            "🔍 总结本周答题规律和易错题型"
        ]
        advice = "**强化期重点：** 大量刷题提升速度，开始限时训练，针对薄弱环节集中突破。"
    elif 0 < days_left <= 30:
        stage, badge, color = "冲刺期", "🔴", "error"
        tasks = [
            "📖 刷 30 道全真模拟题（严格限时）",
            "📝 集中攻克错误率最高的 1 个科目",
            "💡 做 2 套完整模拟卷（还原考场节奏）",
            "🔍 回顾高频考点和历年易错题",
            "🎯 调整作息，保持最佳竞技状态"
        ]
        advice = "**冲刺期重点：** 全真模拟、查漏补缺、调整状态。减少新知识输入，重点巩固高频考点。"
    else:
        st.error("考试日期已过，请重新设置目标日期")
        return

    # ---- 顶部指标 ----
    col1, col2, col3 = st.columns(3)
    col1.metric("⏳ 距离考试", f"{days_left} 天")
    col2.metric("📌 当前阶段", f"{badge} {stage}")
    col3.metric("🗓 目标日期", target_date.strftime("%Y-%m-%d"))

    st.markdown("---")

    # ---- 今日任务 ----
    st.subheader("📋 今日任务清单")
    st.caption("勾选已完成的任务（仅作今日提醒，刷新后重置）")
    for i, task in enumerate(tasks):
        st.checkbox(task, key=f"task_{i}")

    st.markdown("---")

    # ---- 阶段建议 ----
    st.subheader("💡 阶段学习建议")
    if color == "info":
        st.info(advice)
    elif color == "warning":
        st.warning(advice)
    else:
        st.error(advice)

    # ---- 备考进度条 ----
    st.markdown("---")
    st.subheader("📈 备考进度")
    total_days = 90
    elapsed = max(0, total_days - days_left)
    progress = min(elapsed / total_days, 1.0)
    st.progress(progress, text=f"备考进度：约 {int(progress * 100)}%（以90天为参考基准）")


# ==================== 侧边栏 ====================
def render_sidebar():
    """渲染侧边栏，包含 API 配置和功能导航"""
    st.sidebar.title("🎓 公考自学全能助手")
    st.sidebar.markdown("---")

    # ---- API 配置（Key 已填时默认收起）----
    with st.sidebar.expander("⚙️ API 配置", expanded=not st.session_state.get('api_key')):
        api_key = st.text_input(
            "API Key",
            value=st.session_state.get('api_key', ''),
            type="password",
            help="输入你的 API Key（支持 DeepSeek / OpenAI / 其他兼容接口）"
        )
        base_url = st.text_input(
            "Base URL",
            value=st.session_state.get('base_url', DEFAULT_BASE_URL),
            help="DeepSeek：https://api.deepseek.com/v1"
        )
        model_name = st.text_input(
            "Model Name",
            value=st.session_state.get('model_name', DEFAULT_MODEL_NAME),
            help="DeepSeek：deepseek-chat"
        )

        if st.button("💾 保存配置", type="primary"):
            st.session_state.api_key = api_key
            st.session_state.base_url = base_url
            st.session_state.model_name = model_name
            st.success("✅ 配置已保存")
            st.rerun()

    st.sidebar.markdown("---")

    # ---- 题库概况 ----
    total = get_question_count()
    st.sidebar.metric("📦 题库总数", f"{total} 道")

    st.sidebar.markdown("---")

    # ---- 功能导航 ----
    page = st.sidebar.radio(
        "📌 功能导航",
        ["📚 智能笔记总结", "✍️ 智能刷题系统", "📊 备考数据看板", "📅 动态学习计划"]
    )

    st.sidebar.markdown("---")
    st.sidebar.info(
        "**使用说明**\n\n"
        "① 先在 API 配置中填入 Key\n\n"
        "② 笔记总结：上传 PDF/Word，AI 提取考点\n\n"
        "③ 刷题系统：权重抽题，错题优先推送\n\n"
        "④ 数据看板：可视化学习进度和正确率\n\n"
        "⑤ 学习计划：根据考试日期动态生成任务"
    )

    return page


# ==================== 主程序 ====================
def main():
    """应用入口"""
    st.set_page_config(
        page_title="公考自学全能助手",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    init_database()

    # 统一初始化所有 session_state
    for key, default in [
        ('api_key', ''),
        ('base_url', DEFAULT_BASE_URL),
        ('model_name', DEFAULT_MODEL_NAME),
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
    elif page == "✍️ 智能刷题系统":
        page_practice()
    elif page == "📊 备考数据看板":
        page_dashboard()
    elif page == "📅 动态学习计划":
        page_study_plan()


if __name__ == "__main__":
    main()


# ==================== 依赖清单 ====================
# pip install streamlit pdfplumber python-docx openai pandas
