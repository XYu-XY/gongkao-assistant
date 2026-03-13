"""
公考自学全能助手 - 桌面端 Web 应用
技术栈：Streamlit + SQLite + OpenAI API
"""

import streamlit as st
import sqlite3
import json
import random
from datetime import datetime, timedelta
import pandas as pd
from openai import OpenAI
import pdfplumber
from docx import Document

# ==================== 默认配置项 ====================
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL_NAME = "gpt-4o-mini"
# ====================================================


# ==================== 数据库初始化 ====================
def init_database():
    """初始化 SQLite 数据库，创建题目表和答题记录表"""
    conn = sqlite3.connect('gongkao.db', check_same_thread=False)
    cursor = conn.cursor()

    # 创建题目表
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

    # 创建答题记录表
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


def add_question(category, content, options, answer):
    """添加新题目到数据库"""
    try:
        conn = sqlite3.connect('gongkao.db', check_same_thread=False)
        cursor = conn.cursor()
        options_json = json.dumps(options, ensure_ascii=False)
        cursor.execute('''
            INSERT INTO questions (category, content, options, answer)
            VALUES (?, ?, ?, ?)
        ''', (category, content, options_json, answer))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"添加题目失败：{str(e)}")
        return False


def get_weighted_question():
    """按权重抽取一道题目，错题权重更高"""
    try:
        conn = sqlite3.connect('gongkao.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM questions')
        questions = cursor.fetchall()
        conn.close()

        if not questions:
            return None

        # 计算权重：weight = (wrong_count * 2) + 1
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
    """更新题目的正确/错误计数，并记录答题记录"""
    try:
        conn = sqlite3.connect('gongkao.db', check_same_thread=False)
        cursor = conn.cursor()

        if is_correct:
            cursor.execute('UPDATE questions SET correct_count = correct_count + 1 WHERE id = ?', (question_id,))
        else:
            cursor.execute('UPDATE questions SET wrong_count = wrong_count + 1 WHERE id = ?', (question_id,))

        # 记录答题记录
        cursor.execute('''
            INSERT INTO answer_records (question_id, is_correct)
            VALUES (?, ?)
        ''', (question_id, 1 if is_correct else 0))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"更新统计失败：{str(e)}")
        return False


def get_category_stats():
    """获取各科目的答题统计"""
    try:
        conn = sqlite3.connect('gongkao.db', check_same_thread=False)
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
            accuracy = (correct / total * 100) if total > 0 else 0
            stats[category] = {'total': total, 'accuracy': accuracy}

        return stats
    except Exception as e:
        st.error(f"获取统计失败：{str(e)}")
        return {}


def get_daily_stats():
    """获取最近7天的每日答题统计"""
    try:
        conn = sqlite3.connect('gongkao.db', check_same_thread=False)
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

        df = pd.DataFrame(results, columns=['日期', '答题数', '正确数'])
        df['正确率'] = (df['正确数'] / df['答题数'] * 100).round(2)

        return df
    except Exception as e:
        st.error(f"获取每日统计失败：{str(e)}")
        return pd.DataFrame()


# ==================== LLM 调用 ====================
def call_llm(prompt, api_key, base_url, model_name):
    """调用 OpenAI 兼容接口"""
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"LLM 调用失败：{str(e)}")
        return None


# ==================== 文档处理 ====================
def extract_text_from_pdf(file):
    """从 PDF 文件提取文本"""
    try:
        text = ""
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip()
    except Exception as e:
        st.error(f"PDF 解析失败：{str(e)}")
        return None


def extract_text_from_docx(file):
    """从 Word 文档提取文本"""
    try:
        doc = Document(file)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text.strip()
    except Exception as e:
        st.error(f"Word 文档解析失败：{str(e)}")
        return None


def chunk_text(text, max_length=2500):
    """将文本分块，每块最多 max_length 字符"""
    chunks = []
    current_chunk = ""

    for char in text:
        current_chunk += char
        if len(current_chunk) >= max_length:
            chunks.append(current_chunk)
            current_chunk = ""

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def summarize_document(text, api_key, base_url, model_name):
    """对文档进行智能总结"""
    if not text:
        st.error("文档内容为空，无法总结")
        return None

    chunks = chunk_text(text)
    summaries = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, chunk in enumerate(chunks):
        status_text.text(f"正在处理第 {i+1}/{len(chunks)} 块...")

        prompt = f"""请对以下公考学习资料进行结构化总结，使用 Markdown 格式输出，包含：
1. **核心考点**：列出本段的关键知识点
2. **重点记忆项**：需要重点记忆的内容
3. **易混淆点**：容易混淆或出错的地方

资料内容：
{chunk}
"""

        summary = call_llm(prompt, api_key, base_url, model_name)
        if summary:
            summaries.append(summary)

        progress_bar.progress((i + 1) / len(chunks))

    progress_bar.empty()
    status_text.empty()

    return "\n\n---\n\n".join(summaries)


# ==================== 页面功能 ====================
def page_note_summary():
    """智能笔记总结页面"""
    st.header("📚 智能笔记总结")
    st.write("上传 PDF 或 Word 文档，AI 将自动提取核心考点、重点记忆项和易混淆点")

    # 检查 API 配置
    if 'api_key' not in st.session_state or not st.session_state.api_key:
        st.warning("⚠️ 请先在侧边栏配置 API 信息")
        return

    uploaded_file = st.file_uploader("选择文件", type=['pdf', 'docx'])

    if uploaded_file:
        file_type = uploaded_file.name.split('.')[-1].lower()

        if st.button("开始总结", type="primary"):
            with st.spinner("正在解析文档..."):
                if file_type == 'pdf':
                    text = extract_text_from_pdf(uploaded_file)
                elif file_type == 'docx':
                    text = extract_text_from_docx(uploaded_file)
                else:
                    st.error("不支持的文件格式")
                    return

                if text:
                    st.success(f"文档解析成功，共 {len(text)} 字")

                    with st.spinner("正在调用 AI 进行总结..."):
                        summary = summarize_document(
                            text,
                            st.session_state.api_key,
                            st.session_state.base_url,
                            st.session_state.model_name
                        )

                        if summary:
                            st.success("总结完成！")
                            st.markdown("---")
                            st.markdown(summary)


def page_practice():
    """权重错题系统页面"""
    st.header("✍️ 智能刷题系统")

    # 添加题目表单
    with st.expander("➕ 手动添加题目"):
        with st.form("add_question_form"):
            category = st.selectbox("科目分类", ["言语理解", "判断推理", "资料分析", "数量关系", "常识判断"])
            content = st.text_area("题目内容")

            col1, col2 = st.columns(2)
            with col1:
                option_a = st.text_input("选项 A")
                option_b = st.text_input("选项 B")
            with col2:
                option_c = st.text_input("选项 C")
                option_d = st.text_input("选项 D")

            answer = st.selectbox("正确答案", ["A", "B", "C", "D"])

            submitted = st.form_submit_button("添加题目", type="primary")

            if submitted:
                if content and option_a and option_b and option_c and option_d:
                    options = {"A": option_a, "B": option_b, "C": option_c, "D": option_d}
                    if add_question(category, content, options, answer):
                        st.success("题目添加成功！")
                        st.rerun()
                else:
                    st.error("请填写完整的题目信息")

    st.markdown("---")

    # 刷题区域
    if 'current_question' not in st.session_state:
        st.session_state.current_question = None
        st.session_state.user_answer = None
        st.session_state.answered = False

    if st.button("🎲 抽取一道题", type="primary"):
        question = get_weighted_question()
        if question:
            st.session_state.current_question = question
            st.session_state.user_answer = None
            st.session_state.answered = False
            st.rerun()
        else:
            st.warning("题库为空，请先添加题目！")

    if st.session_state.current_question:
        question = st.session_state.current_question

        st.subheader(f"【{question['category']}】")
        st.write(question['content'])

        st.write("**选项：**")
        for key, value in question['options'].items():
            st.write(f"{key}. {value}")

        if not st.session_state.answered:
            user_answer = st.radio("请选择答案", ["A", "B", "C", "D"], key="answer_radio")

            if st.button("提交答案"):
                st.session_state.user_answer = user_answer
                st.session_state.answered = True

                is_correct = (user_answer == question['answer'])
                update_question_stats(question['id'], is_correct)
                st.rerun()
        else:
            is_correct = (st.session_state.user_answer == question['answer'])

            if is_correct:
                st.success(f"✅ 回答正确！正确答案是：{question['answer']}")
            else:
                st.error(f"❌ 回答错误！你的答案：{st.session_state.user_answer}，正确答案：{question['answer']}")

            # 重新获取最新统计数据
            conn = sqlite3.connect('gongkao.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('SELECT correct_count, wrong_count FROM questions WHERE id = ?', (question['id'],))
            result = cursor.fetchone()
            conn.close()

            if result:
                correct_count, wrong_count = result
                st.info(f"📊 本题统计：答对 {correct_count} 次，答错 {wrong_count} 次")
    else:
        st.info("💡 点击上方按钮开始刷题，或先添加题目到题库")


def page_dashboard():
    """备考数据看板页面"""
    st.header("📊 备考数据看板")

    # 各科目统计
    st.subheader("各科目答题统计")
    category_stats = get_category_stats()

    if category_stats:
        df_category = pd.DataFrame([
            {"科目": k, "答题数": v['total'], "正确率": v['accuracy']}
            for k, v in category_stats.items()
        ])

        col1, col2 = st.columns(2)
        with col1:
            st.dataframe(df_category, use_container_width=True)
        with col2:
            st.bar_chart(df_category.set_index('科目')['正确率'])
    else:
        st.info("📝 暂无答题数据，快去刷题系统开始练习吧！")
        st.markdown("""
        **如何开始：**
        1. 前往「✍️ 智能刷题系统」页面
        2. 点击「➕ 手动添加题目」添加题目
        3. 开始刷题后，这里会自动显示统计数据
        """)

    st.markdown("---")

    # 最近7天统计
    st.subheader("最近 7 天答题趋势")
    daily_stats = get_daily_stats()

    if not daily_stats.empty:
        st.dataframe(daily_stats, use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            st.line_chart(daily_stats.set_index('日期')['答题数'])
        with col2:
            st.line_chart(daily_stats.set_index('日期')['正确率'])
    else:
        st.info("📈 暂无最近 7 天的答题数据，坚持每天刷题，这里会展示你的进步曲线！")


def page_study_plan():
    """动态学习计划页面"""
    st.header("📅 动态学习计划")

    target_date = st.date_input("设置考试目标日期", value=datetime.now() + timedelta(days=90))

    today = datetime.now().date()
    days_left = (target_date - today).days

    # 判断阶段
    if days_left > 60:
        stage = "基础期"
        stage_color = "🟢"
        tasks = [
            "📖 刷 10 道判断推理题",
            "📝 阅读 1 篇笔记总结",
            "💡 整理常识判断知识点",
            "🔍 复习昨日错题"
        ]
    elif 30 <= days_left <= 60:
        stage = "强化期"
        stage_color = "🟡"
        tasks = [
            "📖 刷 20 道混合题（各科目均衡）",
            "📝 复习错题集，重点攻克薄弱项",
            "💡 做 1 套模拟试卷（限时）",
            "🔍 总结答题技巧和时间分配"
        ]
    else:
        stage = "冲刺期"
        stage_color = "🔴"
        tasks = [
            "📖 刷 30 道全真模拟题",
            "📝 集中攻克弱项科目",
            "💡 做 2 套完整模拟卷（严格计时）",
            "🔍 回顾高频考点和易错题",
            "🎯 调整心态，保持最佳状态"
        ]

    # 显示倒计时和阶段
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("距离考试", f"{days_left} 天")
    with col2:
        st.metric("当前阶段", f"{stage_color} {stage}")
    with col3:
        st.metric("目标日期", target_date.strftime("%Y-%m-%d"))

    st.markdown("---")

    # 今日任务清单
    st.subheader("📋 今日任务清单")
    for task in tasks:
        st.checkbox(task, key=f"task_{task}")

    st.markdown("---")

    # 阶段建议
    st.subheader("💡 阶段建议")
    if stage == "基础期":
        st.info("""
        **基础期重点：**
        - 系统学习各科目基础知识
        - 建立完整的知识框架
        - 培养良好的学习习惯
        - 不追求速度，注重理解
        """)
    elif stage == "强化期":
        st.warning("""
        **强化期重点：**
        - 大量刷题，提升做题速度
        - 针对性攻克薄弱环节
        - 开始限时模拟训练
        - 总结答题技巧和规律
        """)
    else:
        st.error("""
        **冲刺期重点：**
        - 全真模拟，适应考试节奏
        - 查漏补缺，巩固高频考点
        - 调整作息，保持最佳状态
        - 减少新知识学习，重点复习
        """)


# ==================== 主程序 ====================
def main():
    st.set_page_config(
        page_title="公考自学全能助手",
        page_icon="📚",
        layout="wide"
    )

    # 初始化数据库
    init_database()

    # 初始化 session_state
    if 'api_key' not in st.session_state:
        st.session_state.api_key = ""
    if 'base_url' not in st.session_state:
        st.session_state.base_url = DEFAULT_BASE_URL
    if 'model_name' not in st.session_state:
        st.session_state.model_name = DEFAULT_MODEL_NAME

    # 侧边栏导航
    st.sidebar.title("🎓 公考自学全能助手")
    st.sidebar.markdown("---")

    # API 配置区域
    with st.sidebar.expander("⚙️ API 配置", expanded=not st.session_state.api_key):
        api_key = st.text_input(
            "API Key",
            value=st.session_state.api_key,
            type="password",
            help="输入你的 OpenAI 兼容 API Key"
        )
        base_url = st.text_input(
            "Base URL",
            value=st.session_state.base_url,
            help="API 接口地址，例如：https://api.openai.com/v1"
        )
        model_name = st.text_input(
            "Model Name",
            value=st.session_state.model_name,
            help="模型名称，例如：gpt-4o-mini"
        )

        if st.button("保存配置", type="primary"):
            st.session_state.api_key = api_key
            st.session_state.base_url = base_url
            st.session_state.model_name = model_name
            st.success("✅ 配置已保存")
            st.rerun()

    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "功能导航",
        ["📚 智能笔记总结", "✍️ 智能刷题系统", "📊 备考数据看板", "📅 动态学习计划"]
    )

    st.sidebar.markdown("---")
    st.sidebar.info("""
    **使用提示：**
    - 笔记总结：上传学习资料，AI 自动提取考点
    - 刷题系统：智能推荐错题，巩固薄弱环节
    - 数据看板：可视化学习进度和正确率
    - 学习计划：根据考试日期动态调整任务
    """)

    # 页面路由
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
# 依赖：streamlit, pdfplumber, python-docx, openai, pandas
# 安装命令：pip install streamlit pdfplumber python-docx openai pandas
