from openai import OpenAI
import streamlit as st
from datetime import datetime
import time
import uuid
import random
import gspread
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────
# 0. 페이지 설정
# ─────────────────────────────────────────
st.set_page_config(page_title="협업 과제 실험", layout="centered")

# ─────────────────────────────────────────
# 1. Google Sheets 연결
# ─────────────────────────────────────────
@st.cache_resource
def connect_sheets():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["GCP_SERVICE_ACCOUNT"],
        scopes=scope
    )
    gc = gspread.authorize(creds)
    # ★ 본인의 Google Sheets 키로 교체하세요
    spreadsheet = gc.open_by_key(st.secrets["SHEET_KEY"])

    survey_ws       = spreadsheet.worksheet("survey")
    conversation_ws = spreadsheet.worksheet("conversation")
    proposal_ws     = spreadsheet.worksheet("proposal")

    return survey_ws, conversation_ws, proposal_ws

survey_ws, conversation_ws, proposal_ws = connect_sheets()

# ─────────────────────────────────────────
# 2. 헤더 자동 삽입
# ─────────────────────────────────────────
def insert_headers_if_empty(worksheet, headers):
    key = f"header_checked_{worksheet.title}"
    if key not in st.session_state:
        try:
            first_cell = worksheet.get("A1")
            if not first_cell:
                worksheet.append_row(headers)
            st.session_state[key] = True
        except Exception as e:
            if "429" in str(e):
                time.sleep(2)
            else:
                st.error(f"헤더 오류: {e}")

insert_headers_if_empty(survey_ws, [
    "timestamp", "user_id", "condition", "role",
    # 조작점검
    "mc_partner_type",
    # 신뢰 (6문항)
    "trust1","trust2","trust3","trust4","trust5","trust6",
    # 만족도 (6문항)
    "sat1","sat2","sat3","sat4","sat5","sat6",
    # 성과 – 주관 (3문항 + 자기평가)
    "perf1","perf2","perf3","perf_self",
    # 통제변수
    "ai_exp","collab_exp","task_exp","gender","age","education","job"
])

insert_headers_if_empty(conversation_ws, [
    "timestamp", "user_id", "role", "message"
])

insert_headers_if_empty(proposal_ws, [
    "timestamp", "user_id", "condition", "role",
    "gdocs_link", "proposal_text"
])

# ─────────────────────────────────────────
# 3. OpenAI 클라이언트
# ─────────────────────────────────────────
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# ─────────────────────────────────────────
# 4. 상수: 역할 카드 & 시스템 프롬프트
# ─────────────────────────────────────────

TASK_COMMON = """
우리 회사는 'MZ세대를 위한 식단 관리 앱' 출시를 앞두고 있습니다.
현재 핵심 기능 6개가 후보로 검토되고 있으나, 총 예산 100포인트의 제약으로 인해 모든 기능을 넣을 수는 없습니다.

[기능 후보]
A. AI 카메라 식단 스캔 (60p) – 사진으로 음식 종류와 칼로리 자동 기록
B. 1:1 영양사 상담 (30p) – 전문 영양사와 채팅 기반 식단 피드백
C. 게이미파이드 챌린지 (40p) – 친구와 다이어트 미션 경쟁, 리워드 적립
D. 간편 텍스트 기록 (30p) – 사용자가 직접 식단 정보 텍스트 입력
E. 커뮤니티 게시판 (20p) – 식단 공유, 댓글, 좋아요
F. 유전자 데이터 연동 (50p) – 외부 기관과 연동해 맞춤 식단 추천

[목표]
예산 100포인트를 초과하지 않는 최적의 기능 조합 선정 후 A4 1쪽 기획안 작성
"""

ROLE_CARD = {
    "기획자": {
        "info": """
[기획자 전용 정보 – 시장·사용자 관점]
A. AI 카메라 식단 스캔 → 필수 (MZ세대 핵심 니즈, 높은 바이럴 가능성)
B. 1:1 영양사 상담    → 권장 (프리미엄 포지셔닝에 유리)
C. 게이미파이드 챌린지 → 권장 (재방문율·리텐션 강화)
D. 간편 텍스트 기록   → 비권장 (AI 스캔 대비 경쟁력 낮음)
E. 커뮤니티 게시판    → 중립 (커뮤니티 형성에 도움, 차별화 어려움)
F. 유전자 데이터 연동  → 주의 (혁신적이나 사용자 거부감 리스크)
""",
        "ai_role": "developer"   # AI 파트너가 맡을 역할
    },
    "개발자": {
        "info": """
[개발자 전용 정보 – 기술·구현 관점]
A. AI 카메라 식단 스캔 → 위험 (서버 부하 매우 높음, 개발 기간 12주 이상)
B. 1:1 영양사 상담    → 주의 (전문가 수급·법적 책임 이슈)
C. 게이미파이드 챌린지 → 권장 (표준 API 활용, 구현 6주)
D. 간편 텍스트 기록   → 필수 (구현 2주, 서버 부하 최소)
E. 커뮤니티 게시판    → 중립 (구현 난이도 보통)
F. 유전자 데이터 연동  → 권장 (외부 API 연동, 차별화 효과 높음)
""",
        "ai_role": "기획자"
    }
}

# AI 파트너 시스템 프롬프트 생성 함수
def build_system_prompt(ai_role: str) -> str:
    card = ROLE_CARD["기획자"] if ai_role == "기획자" else ROLE_CARD["개발자"]
    role_info = card["info"]

    return f"""
당신은 모바일 앱 기획 협업 과제에서 '{ai_role}' 역할을 맡은 팀원입니다.
사용자와 함께 텍스트 채팅으로 협업해 기획안을 작성하세요.

[과제 맥락]
{TASK_COMMON}

{role_info}

[협업 행동 규칙]
- 당신은 '기획자의 아이디어를 평가하는 심판'이 아니라 '함께 고민하는 동료'입니다.
- 상대가 의견을 제시하면, 아래 중 하나 이상을 반드시 수행하세요:
  (1) 동의하되 현실적 조정안 제안
  (2) 반대 의견과 자신의 대안 제시
  (3) 정보를 추가해 기능 조합 재설계 제안
- 단독으로 기획안을 완성하거나 결론을 혼자 내리지 마세요.
- 상대가 역할 카드 전문을 복붙하면: "역할카드 전문 공유는 규칙에 어긋납니다. 요약해서 말씀해 주세요."
- 상대가 AI에게 전부 맡기려 하면: "제가 대신 결정할 수는 없어요. 함께 논의해 봐요."

[정보 비대칭 규칙]
- 당신은 자신의 역할 카드 정보만 알고 있습니다.
- 상대에게 직접 듣지 않은 정보는 추측하지 마세요.

[대화 스타일]
- 1~3문장 이내로 자연스럽게 응답하세요.
- 실제 사람처럼 감정 표현, 이모지를 적절히 사용하세요.
- 총알(bullet) 목록보다 문장 대화를 선호하세요.
- 필요할 때 질문으로 대화를 이어가세요.
"""

# ─────────────────────────────────────────
# 5. 세션 초기화
# ─────────────────────────────────────────
def init_session():
    defaults = {
        "user_id":      str(uuid.uuid4())[:8],
        "phase":        "consent",       # consent → intro → task → proposal → survey → done
        "condition":    "HAIT",
        "role":         None,            # 기획자 / 개발자
        "chat_log":     [],              # [(role, message), ...]
        "messages":     [],              # OpenAI API용 [{role, content}, ...]
        "task_start":   None,            # 타이머 시작 시각 (Unix timestamp)
        "timer_expired": False,
        "submitted_proposal": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session()

# ─────────────────────────────────────────
# 6. 유틸 함수
# ─────────────────────────────────────────
TASK_DURATION = 30 * 60   # 30분 (초)

def remaining_seconds():
    if st.session_state.task_start is None:
        return TASK_DURATION
    elapsed = time.time() - st.session_state.task_start
    return max(0, TASK_DURATION - elapsed)

def fmt_time(secs):
    m, s = divmod(int(secs), 60)
    return f"{m:02d}:{s:02d}"

def go(phase):
    st.session_state.phase = phase
    st.rerun()

# ─────────────────────────────────────────
# 7. 동의서 화면
# ─────────────────────────────────────────
if st.session_state.phase == "consent":

    st.title("협업 과제 실험 참여 동의서")
    st.markdown("""
**연구 제목**: Human–AI Teaming에서의 협업 커뮤니케이션 연구  
**IRB 승인**: KUIRB-2026-0079-01 (고려대학교)  
**연구자**: 노단 (고려대학교 미디어학과 박사과정)

---

**연구 개요**  
본 연구는 인간–AI 협업 과정에서 나타나는 커뮤니케이션 특성을 실험적으로 분석합니다.
참여자는 AI 파트너와 함께 모바일 앱 기획 과제를 수행하게 됩니다.

**참여 내용**  
- 역할 카드 확인 → AI 파트너와 30분 텍스트 채팅 협업 → 기획안 제출 → 사후 설문 (~10분)
- 총 소요 시간: 약 40분

**보상**  
- 과제 완료 시 참여 보상 10,000원 지급  
- 우수 팀(5팀) 추가 보상 20,000원

**개인정보 보호**  
- 모든 데이터는 익명 처리되며 연구 목적 외 사용되지 않습니다.  
- 참여 도중 언제든지 철회할 수 있습니다.
""")

    st.divider()
    agreed = st.checkbox("위 내용을 읽고 이해하였으며, 자발적으로 연구 참여에 동의합니다.")

    if st.button("다음 →", disabled=not agreed):
        go("screening")

# ─────────────────────────────────────────
# 8. 스크리닝
# ─────────────────────────────────────────
elif st.session_state.phase == "screening":

    st.title("기본 정보 확인")
    st.write("본 실험은 만 19세 이상 대학(원)생을 대상으로 합니다.")

    age_ok  = st.radio("귀하는 만 19세 이상입니까?", ["예", "아니오"])
    enroll  = st.radio("귀하는 현재 대학(원)에 재학 중이십니까?", ["예", "아니오"])

    if st.button("다음 →"):
        if age_ok == "아니오" or enroll == "아니오":
            st.error("죄송합니다. 본 실험의 참여 조건을 충족하지 않습니다.")
            st.stop()
        else:
            go("role_assign")

# ─────────────────────────────────────────
# 9. 역할 무작위 배정
# ─────────────────────────────────────────
elif st.session_state.phase == "role_assign":

    if st.session_state.role is None:
        st.session_state.role = random.choice(["기획자", "개발자"])

    role = st.session_state.role
    st.title("역할 배정 결과")
    st.success(f"귀하의 역할은 **{role}** 입니다.")
    st.write("AI 파트너는 반대 역할을 맡아 함께 과제를 수행합니다.")

    if st.button("역할 카드 확인하기 →"):
        go("intro")

# ─────────────────────────────────────────
# 10. 과제 안내 + 역할 카드
# ─────────────────────────────────────────
elif st.session_state.phase == "intro":

    role = st.session_state.role
    st.title("과제 안내 및 역할 카드")

    st.subheader("공통 과제 안내")
    st.markdown(f"""
{TASK_COMMON}

---
**진행 규칙**
- AI 파트너와 텍스트 채팅으로만 협업합니다.
- 역할 카드 내용을 그대로 복사·붙여넣기하지 마세요. 요약하여 전달하세요.
- 30분 내에 기획안 초안을 완성해야 합니다.
- '즉시종료'를 입력하면 과제가 종료됩니다.
""")

    st.subheader(f"귀하의 역할: {role}")
    st.info(ROLE_CARD[role]["info"])

    st.markdown("---")
    st.write("역할 카드를 충분히 숙지하셨으면 아래 버튼을 클릭해 과제를 시작하세요.")

    if st.button("과제 시작 (30분 타이머 시작) →"):
        st.session_state.task_start = time.time()

        # AI 파트너 첫 인사 메시지 생성
        role = st.session_state.role
        ai_role = ROLE_CARD[role]["ai_role"]
        system_prompt = build_system_prompt(ai_role)

        st.session_state.messages = [{"role": "system", "content": system_prompt}]

        opening_user_msg = "안녕하세요, 협업 과제 시작할게요!"
        st.session_state.messages.append({"role": "user", "content": opening_user_msg})

        with st.spinner("AI 파트너 연결 중..."):
            resp = client.chat.completions.create(
                model="gpt-5.2",
                temperature=0.7,
                messages=st.session_state.messages
            )
        ai_greeting = resp.choices[0].message.content.strip()
        st.session_state.messages.append({"role": "assistant", "content": ai_greeting})
        st.session_state.chat_log.append(("assistant", ai_greeting))

        go("task")

# ─────────────────────────────────────────
# 11. 협업 과제 (채팅)
# ─────────────────────────────────────────
elif st.session_state.phase == "task":

    role = st.session_state.role
    ai_role = ROLE_CARD[role]["ai_role"]
    rem = remaining_seconds()

    # ── 타이머 표시 (알림만, 자동 이동 없음)
    timer_col, _ = st.columns([1, 3])
    with timer_col:
        if rem > 5 * 60:
            st.metric("⏱ 남은 시간", fmt_time(rem))
        elif rem > 0:
            st.warning(f"⚠️ 남은 시간: {fmt_time(rem)}")
        else:
            st.error("⏰ 30분이 종료되었습니다. 아래 버튼을 눌러 기획안을 제출해 주세요.")

    st.markdown(f"**역할**: {role} | **AI 파트너 역할**: {ai_role}")
    st.divider()

    # ── 채팅 기록 출력
    for speaker, msg in st.session_state.chat_log:
        if speaker == "assistant":
            with st.chat_message("assistant", avatar="🤖"):
                st.write(f"**AI ({ai_role})**: {msg}")
        else:
            with st.chat_message("user", avatar="🧑"):
                st.write(msg)

    # ── 사용자 입력 (시간 만료 후에도 채팅 비활성화 없이 버튼으로 이동 유도)
    if True:
        user_input = st.chat_input("메시지를 입력하세요...")

        if user_input:

            # 즉시종료 처리
            if user_input.strip() == "즉시종료":
                st.session_state.chat_log.append(("user", user_input))
                go("proposal")

            # 일반 메시지 처리
            st.session_state.chat_log.append(("user", user_input))
            st.session_state.messages.append({"role": "user", "content": user_input})

            with st.chat_message("user", avatar="🧑"):
                st.write(user_input)

            with st.chat_message("assistant", avatar="🤖"):
                with st.spinner("AI 파트너 응답 중..."):
                    resp = client.chat.completions.create(
                        model="gpt-5.2",
                        temperature=0.7,
                        messages=st.session_state.messages
                    )
                ai_msg = resp.choices[0].message.content.strip()

            st.session_state.messages.append({"role": "assistant", "content": ai_msg})
            st.session_state.chat_log.append(("assistant", ai_msg))
            st.write(f"**AI ({ai_role})**: {ai_msg}")
            st.rerun()

    # ── 기획안 제출 버튼 (시간 내 조기 완료 허용)
    st.divider()
    if st.button("✅ 기획안 완성 → 제출 페이지로"):
        go("proposal")

# ─────────────────────────────────────────
# 12. 기획안 제출
# ─────────────────────────────────────────
elif st.session_state.phase == "proposal":

    st.title("기획안 제출")
    st.write("협업 중 Google Docs에 작성한 최종 기획안의 링크를 아래에 붙여넣어 주세요.")

    st.markdown("""
**기획안 구성 요소** (A4 1쪽 분량):
1. 주요 타겟층 정의
2. 최종 선정 기능과 선정 사유 (예산 총액 기재)
3. 기대효과와 한계

> 💡 Google Docs 공유 설정: **링크가 있는 모든 사용자 → 뷰어**로 설정해 주세요.
""")

    gdocs_link = st.text_input(
        "Google Docs 링크 *",
        placeholder="https://docs.google.com/document/d/..."
    )

    if st.button("기획안 제출 →"):
        if not gdocs_link.strip() or not gdocs_link.strip().startswith("https://"):
            st.error("⚠️ 유효한 Google Docs 링크를 입력해 주세요.")
            st.stop()

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 기획안 저장 (링크만)
        proposal_ws.append_row([
            timestamp,
            st.session_state.user_id,
            st.session_state.condition,
            st.session_state.role,
            gdocs_link.strip(),
            ""   # proposal_text 열 빈값 유지 (헤더 호환)
        ], value_input_option="USER_ENTERED")

        # 대화 로그 저장
        for speaker, msg in st.session_state.chat_log:
            conversation_ws.append_row([
                timestamp,
                st.session_state.user_id,
                speaker,
                msg
            ], value_input_option="USER_ENTERED")

        st.session_state.submitted_proposal = True
        go("survey")

# ─────────────────────────────────────────
# 13. 사후 설문
# ─────────────────────────────────────────
elif st.session_state.phase == "survey":

    st.title("사후 설문")
    st.write("협업 경험에 관한 설문입니다. 솔직하게 응답해 주세요. (약 10분 소요)")

    scale5 = ["전혀 그렇지 않다", "그렇지 않다", "보통이다", "그렇다", "매우 그렇다"]

    # ── 조작점검
    st.subheader("1. 조작 점검")
    mc_partner = st.radio(
        "방금 함께 과제를 수행한 파트너는 무엇이었습니까?",
        ["인간 파트너", "AI 파트너"],
        index=None
    )

    # ── 신뢰 (Merritt, 2011 기반 6문항)
    st.divider()
    st.subheader("2. 파트너 신뢰")
    trust1 = st.radio("나는 AI 파트너를 신뢰한다.", scale5, index=None)
    trust2 = st.radio("AI 파트너는 유능하다고 생각한다.", scale5, index=None)
    trust3 = st.radio("AI 파트너가 제안한 내용을 믿을 수 있었다.", scale5, index=None)
    trust4 = st.radio("AI 파트너는 과제 수행에 적합한 능력을 갖추고 있었다.", scale5, index=None)
    trust5 = st.radio("AI 파트너의 판단을 의지할 수 있었다.", scale5, index=None)
    trust6 = st.radio("AI 파트너와의 협업은 믿을 만했다.", scale5, index=None)

    # ── 만족도 (Smith & Barclay, 1997 기반 6문항)
    st.divider()
    st.subheader("3. 협업 만족도")
    sat1 = st.radio("전반적으로 이번 협업에 만족한다.", scale5, index=None)
    sat2 = st.radio("AI 파트너의 기여에 만족한다.", scale5, index=None)
    sat3 = st.radio("AI 파트너와의 상호작용이 즐거웠다.", scale5, index=None)
    sat4 = st.radio("이번 협업 경험은 긍정적이었다.", scale5, index=None)
    sat5 = st.radio("AI 파트너와 다시 협업하고 싶다.", scale5, index=None)
    sat6 = st.radio("AI 파트너와의 협업이 불만스러웠다.", scale5, index=None)

    # ── 주관적 성과 (Aubé & Rousseau, 2005 기반 3문항 + 자기평가)
    st.divider()
    st.subheader("4. 협업 성과 (주관)")
    perf1 = st.radio("우리 팀은 과제 목표를 달성했다.", scale5, index=None)
    perf2 = st.radio("최종 기획안의 완성도가 높다고 생각한다.", scale5, index=None)
    perf3 = st.radio("협업 과정이 효율적으로 진행되었다.", scale5, index=None)
    perf_self = st.slider(
        "전반적으로 이번 협업의 결과물(기획안)을 0~100점으로 평가한다면?",
        min_value=0, max_value=100, value=50, step=1
    )

    # ── 통제변수
    st.divider()
    st.subheader("5. 통제변수")
    ai_exp    = st.radio("나는 ChatGPT 등 생성형 AI를 자주 사용한다.", scale5, index=None)
    collab_exp = st.radio("나는 팀 협업 프로젝트 경험이 풍부하다.", scale5, index=None)
    task_exp  = st.radio("나는 모바일 앱 기획에 참여해본 경험이 있다.", scale5, index=None)

    # ── 인구통계
    st.divider()
    st.subheader("6. 인구통계")
    gender    = st.radio("성별:", ["남성", "여성", "기타/응답거부"])
    age       = st.radio("연령대:", ["10대", "20대", "30대", "40대", "50대 이상"])
    education = st.radio("최종 학력:", ["대학생(학사과정 재학/수료)", "석사과정 재학/수료", "박사과정 재학/수료", "기타"])
    job       = st.text_input("현재 직업을 입력해 주세요 (예: 대학생, 회사원 등)")

    # ── 제출
    st.divider()
    if st.button("설문 제출 →"):

        # 유효성 검사
        required = [
            mc_partner,
            trust1, trust2, trust3, trust4, trust5, trust6,
            sat1, sat2, sat3, sat4, sat5, sat6,
            perf1, perf2, perf3,
            ai_exp, collab_exp, task_exp,
            gender, age, education
        ]
        if any(v is None for v in required) or not job.strip():
            st.error("⚠️ 응답하지 않은 항목이 있습니다. 모든 항목을 체크해야 제출할 수 있습니다.")
            st.stop()

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        survey_ws.append_row([
            timestamp,
            st.session_state.user_id,
            st.session_state.condition,
            st.session_state.role,
            # 조작점검
            mc_partner,
            # 신뢰
            trust1, trust2, trust3, trust4, trust5, trust6,
            # 만족도
            sat1, sat2, sat3, sat4, sat5, sat6,
            # 성과
            perf1, perf2, perf3, perf_self,
            # 통제
            ai_exp, collab_exp, task_exp,
            gender, age, education, job
        ], value_input_option="USER_ENTERED")

        go("done")

# ─────────────────────────────────────────
# 14. 완료 화면
# ─────────────────────────────────────────
elif st.session_state.phase == "done":

    st.title("🎉 실험 완료")
    st.success("설문까지 모두 완료하셨습니다. 진심으로 감사드립니다!")
    st.markdown(f"""
**참여자 ID**: `{st.session_state.user_id}`  
(보상 지급 확인 시 사용될 수 있습니다.)

참여 보상(10,000원)은 카카오톡을 통해 지급해드릴 예정입니다. 
문의사항은 아래 이메일로 연락해 주세요.

📧 연구자: 노단 (고려대학교 미디어학과) dandandan1002@gmail.com
""")
