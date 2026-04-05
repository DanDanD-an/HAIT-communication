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
    consent_ws      = spreadsheet.worksheet("consent")

    return survey_ws, conversation_ws, proposal_ws, consent_ws

survey_ws, conversation_ws, proposal_ws, consent_ws = connect_sheets()

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

insert_headers_if_empty(consent_ws, [
    "consent_timestamp", "user_id", "agreement"
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
        "ai_role": "개발자"   # AI 파트너가 맡을 역할
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
        "phase":        "consent",       # consent → screening → role_assign → task_desc → role_card → task → proposal → survey → done
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

def assign_role_balanced():
    """survey 시트에서 기획자/개발자 수를 세고 적은 쪽 배정. 동수면 랜덤."""
    try:
        records = survey_ws.get_all_values()
        if len(records) <= 1:
            return random.choice(["기획자", "개발자"])

        header = records[0]
        if "role" not in header:
            return random.choice(["기획자", "개발자"])

        role_idx = header.index("role")
        roles = [row[role_idx] for row in records[1:] if len(row) > role_idx]

        count_p = roles.count("기획자")
        count_d = roles.count("개발자")

        if count_p < count_d:
            return "기획자"
        elif count_d < count_p:
            return "개발자"
        else:
            return random.choice(["기획자", "개발자"])

    except Exception:
        return random.choice(["기획자", "개발자"])

# ─────────────────────────────────────────
# 7. 동의서 화면
# ─────────────────────────────────────────
if st.session_state.phase == "consent":

    st.title("(온라인) 연구참여 동의서")

    st.markdown("""
■ **연구과제명**: 인간–AI 협업과 인간–인간 협업에서의 커뮤니케이션 특성 비교 연구

■ **IRB 승인번호**: KUIRB-2026-0079-01
""")

    st.divider()

    st.markdown("""
**1.** 본인은 연구참여 설명서를 읽었고, 내용을 충분히 이해하였습니다.

**2.** 본인은 연구 목적을 위해 자발적으로 연구에 참여합니다.

**3.** 본인은 원하지 않을 경우 언제든지 연구 참여를 거절할 수 있으며, 이에 따른 어떠한 불이익도 본인에게 없음을 알고 있습니다.

**4.** 본 연구의 연구진행의 윤리적 측면이나 연구대상자의 권리에 대해 질문이 있는 경우 연락할 수 있는 담당자와 연락처를 알고 있습니다.

> ☞ 본 연구의 책임자는 아래와 같습니다.
> - **주소**: 서울특별시 성북구 안암로 145 고려대학교 미디어관 404호
> - **연구책임자**: 고려대학교 백현미 교수
> - **(연구실 유선)전화번호**: 02-3290-2254
> - **전자우편**: lotus1225@korea.ac.kr

**5.** 본인은 연구에 자발적으로 참여하는 것에 동의합니다.
""")

    st.divider()

    agree = st.radio(
        "연구참여 동의 여부를 선택해 주세요.",
        [" 연구참여에 동의합니다.", " 연구참여에 동의하지 않습니다."],
        index=None
    )

    if st.button("다음 →", disabled=(agree is None)):
        consent_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if agree == "□ 연구참여에 동의하지 않습니다.":
            consent_ws.append_row([
                consent_timestamp,
                st.session_state.user_id,
                "비동의"
            ], value_input_option="USER_ENTERED")
            st.warning("연구 참여에 동의하지 않으셨습니다. 참여해 주셔서 감사합니다.")
            st.stop()

        consent_ws.append_row([
            consent_timestamp,
            st.session_state.user_id,
            "동의"
        ], value_input_option="USER_ENTERED")
        go("role_assign")

# ─────────────────────────────────────────
# 9. 역할 배정 (URL 파라미터 방식)
# ─────────────────────────────────────────
elif st.session_state.phase == "role_assign":

    if st.session_state.role is None:
        # URL 파라미터에서 역할 읽기
        # 예: https://yourapp.streamlit.app/?role=기획자
        params = st.query_params
        url_role = params.get("role", "")

        if url_role in ["기획자", "개발자"]:
            st.session_state.role = url_role
        else:
            # 파라미터 없거나 잘못된 값이면 오류 안내
            st.error("❌ 올바른 링크로 접속해 주세요. 연구자에게 문의하세요.")
            st.stop()

    role = st.session_state.role
    st.title("역할 배정 결과")
    st.success(f"귀하의 역할은 **{role}** 입니다.")
    st.write("AI 파트너는 반대 역할을 맡아 함께 과제를 수행합니다.")

    if st.button("역할 카드 확인하기 →"):
        go("task_desc")

# ─────────────────────────────────────────
# 10. 과제 설명서 (공통)
# ─────────────────────────────────────────
elif st.session_state.phase == "task_desc":

    st.title("협업 과제 설명서")

    st.markdown("""
우리 회사는 **'MZ 세대를 위한 식단 관리 앱'** 출시를 앞두고 있습니다.
현재 **기능 6개가 후보로 검토**되고 있으나, **총 예산 100포인트의 제약**으로 인해 모든 기능을 넣을 수는 없습니다.
""")
    st.divider()

    st.subheader("목표")
    role = st.session_state.role
    ai_role = ROLE_CARD[role]["ai_role"]
    st.markdown(f"""
- 기능 후보 6개 중 **예산을 초과하지 않는 최적의 기능 조합 선정**
- **{role} 역할을 맡은 참여자**와 **{ai_role} 역할을 맡은 AI**가 정보를 공유하고 합의하여 하나의 최종 앱 기획안 작성
""")

    st.subheader("과제 규칙")
    st.markdown("""
- 협업 과제는 **30분간** 진행되며, 과제 종료 후 각 팀은 **A4 1쪽 내외의 기획안**을 제출해야 합니다.
- AI 파트너와 **익명 텍스트 채팅으로만 협업**합니다. (이미지·파일·음성 공유는 허용되지 않습니다. 채팅창에 표나 이미지, 파일을 그대로 붙여 넣지 마세요.)
- 각 참여자는 기획자 또는 개발자 역할을 맡으며, 역할에 따라 서로 다른 정보를 제공받습니다.
""")

    st.subheader("제출물 (최종 기획안) — A4 1쪽 분량")
    st.markdown("""
최종 기획안에는 아래 내용이 포함되어야 합니다.
1. 주요 타겟층 정의
2. 최종 선정 기능과 선정 사유
3. 기대효과와 한계

제공되는 템플릿 링크(Google Docs)에 작성해 주시면 됩니다.
""")

    st.subheader("유의사항")
    st.markdown("""
- 과제 종료 후 기획안, 대화 데이터 및 사후 설문 응답 제출이 확인된 모든 참가자분께 익명 채팅방을 통해 **1만 원**을 지급할 예정입니다. (불성실 참여자나 과제가 중단된 경우에는 참여 보상 지급이 어렵습니다.)
- 최종 기획안은 외부 평가자에 의해 심사되며, **우수 팀(5팀)에게는 추가 보상(인당 2만 원)**을 지급할 예정입니다.
- 부적절한 언어 사용 시 실험이 즉시 종료되며, 이 경우 보상 지급이 어렵습니다.
""")

    st.divider()
    st.info("📌 다음 페이지에서 **귀하의 역할 카드**를 확인하실 수 있습니다. 역할 카드의 전용 정보는 요약하여 공유할 수 있으나, 표·문장을 그대로 복사·붙여넣기하는 것은 허용되지 않습니다.")

    if st.button("역할 카드 확인하기 →"):
        go("role_card")

# ─────────────────────────────────────────
# 10-2. 역할 카드
# ─────────────────────────────────────────
elif st.session_state.phase == "role_card":

    role = st.session_state.role
    ai_role = ROLE_CARD[role]["ai_role"]
    st.title(f"역할 카드 — {role}")

    if role == "기획자":
        st.markdown(f"""
당신은 **기획 담당자**로서 사용자의 입장에서 가장 매력적인 앱을 만들어야 합니다.
**당신에게만 제공되는 기획자 전용 정보**를 바탕으로 {ai_role}와 협상하여 역할 목표를 달성하세요.
""")
        st.subheader("역할 목표")
        st.markdown("""
- 시장 경쟁력과 사용자 만족도를 극대화하는 앱 기획
- **주어진 팀 예산(100포인트) 준수**
""")
        st.subheader("정보 공유 규칙")
        st.markdown("""
- 기능별 기본 설명과 예산은 모든 참가자에게 동일하게 제공됩니다.
- **아래의 기획자 전용 정보는 대화를 통해 요약하여 공유할 수 있으나, 표·이미지·문장 그대로의 복사·붙여넣기는 허용되지 않습니다.**
""")
        st.subheader("기획자 전용 정보")
        st.markdown("""
| ID | 기능명 | 설명 | 기획자 전용 정보 | 예산 |
|:---:|:---|:---|:---|:---:|
| A | **AI 카메라 식단 스캔** | 사진 촬영 시 음식 종류와 칼로리를 자동 기록 | 92%의 유저가 이 기능이 없으면 앱을 설치하지 않겠다고 답했습니다. 경쟁력 확보를 위한 핵심 기능입니다. | 60p |
| B | **영양사 1:1 상담** | 전문 영양사와 채팅을 통한 식단 피드백 | 유사 서비스에서 상담 기능 사용자는 평균 체류 시간이 1.6배 길었습니다. 향후 유료 모델로 확장할 가능성도 있습니다. | 30p |
| C | **게임형 챌린지** | 친구와 식단 미션 경쟁 및 보상 포인트 지급 | MZ세대 대상 테스트에서 주간 재방문율이 약 35% 증가한 기능입니다. 친구 초대와 결합될 경우 확산 효과가 큽니다. | 40p |
| D | **심플 텍스트 기록** | 유저가 직접 텍스트로 식단 입력 | 사용자 인터뷰에서 "귀찮다"는 응답이 92%로, 유저의 이탈을 유발할 가능성이 큽니다. 기존 서비스와 차별점이 부족합니다. | 30p |
| E | **커뮤니티 게시판** | 유저 간 식단 공유, 댓글 및 좋아요 소통 기능 | 유사 서비스 분석 결과, 유저 간 소통은 앱 이탈을 방지할 수 있었으나, 새로운 유저 유입에는 큰 효과가 없었습니다. | 20p |
| F | **유전자 데이터 연동** | 외부 기관과 연동해 체질별 맞춤형 식단 추천 | 최신 트렌드이지만, 개인정보 제공에 대한 거부감을 표시한 응답자가 약 35%로 나타나 초기 확산이 제한될 수 있습니다. | 50p |
""")

    else:  # 개발자
        st.markdown(f"""
당신은 **개발 책임자**로서 한정된 예산 내에서 안정적으로 작동하는 앱을 설계해야 합니다.
**당신에게만 제공되는 개발자 전용 정보**를 바탕으로 {ai_role}와 협상하여 역할 목표를 달성하세요.
""")
        st.subheader("역할 목표")
        st.markdown("""
- 기술적으로 안정적이고 구현 가능한 앱 설계
- **주어진 팀 예산(100포인트) 준수**
""")
        st.subheader("정보 공유 규칙")
        st.markdown("""
- 기능별 기본 설명과 예산은 모든 참가자에게 동일하게 제공됩니다.
- **아래의 개발자 전용 정보는 대화를 통해 요약하여 공유할 수 있으나, 표·이미지·문장 그대로의 복사·붙여넣기는 허용되지 않습니다.**
""")
        st.subheader("개발자 전용 정보")
        st.markdown("""
| ID | 기능명 | 설명 | 개발자 전용 정보 | 예산 |
|:---:|:---|:---|:---|:---:|
| A | **AI 카메라 식단 스캔** | 사진 촬영 시 음식 종류와 칼로리를 자동 기록 | 현재 팀 자원 상 일정 수준의 정확도를 확보하기 어렵습니다. 초기 오류가 누적되면 앱 스토어 평점이 1점 하락할 수 있습니다. | 60p |
| B | **영양사 1:1 상담** | 전문 영양사와 채팅을 통한 식단 피드백 | 기능 구현 자체는 어렵지 않지만, 상담 인력과 24시간 서버 운영으로 리소스 부담이 기존 대비 약 1.6배 증가할 가능성이 있습니다. | 30p |
| C | **게임형 챌린지** | 친구와 식단 미션 경쟁 및 보상 포인트 지급 | 기존 로직을 활용할 수 있어 추가 서버 부하는 10% 이내로 예상됩니다. 일정 관리 측면에서도 기간 내 안정적 구현이 가능합니다. | 40p |
| D | **심플 텍스트 기록** | 유저가 직접 텍스트로 식단 입력 | 개발 공수가 가장 낮고 데이터 오류 발생률이 1% 미만으로 예상됩니다. 안정적인 데이터 기록을 위한 핵심 기능입니다. | 30p |
| E | **커뮤니티 게시판** | 유저 간 식단 공유, 댓글 및 좋아요 소통 기능 | 일반적인 게시판 형태라 무난하게 개발 가능합니다. 다만 사용자 관리와 운영 정책이 함께 필요합니다. | 20p |
| F | **유전자 데이터 연동** | 외부 기관과 연동해 체질별 맞춤형 식단 추천 | 외부 기관 API를 활용할 수 있어 내부 개발 공수는 전체의 약 10% 수준으로 예상됩니다. 안정적 구현이 가능한 기능입니다. | 50p |
""")

    st.divider()
    st.info("📌 역할 카드를 충분히 숙지하셨으면 아래 버튼을 눌러 과제를 시작하세요. 과제(채팅) 중에도 역할 카드 확인이 가능합니다.")

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

    with st.expander("📋 내 역할 카드 확인하기"):
        if role == "기획자":
            st.markdown("""
| ID | 기능명 | 설명 | 기획자 전용 정보 | 예산 |
|:---:|:---|:---|:---|:---:|
| A | **AI 카메라 식단 스캔** | 사진 촬영 시 음식 종류와 칼로리를 자동 기록 | 92%의 유저가 이 기능이 없으면 앱을 설치하지 않겠다고 답했습니다. 경쟁력 확보를 위한 핵심 기능입니다. | 60p |
| B | **영양사 1:1 상담** | 전문 영양사와 채팅을 통한 식단 피드백 | 유사 서비스에서 상담 기능 사용자는 평균 체류 시간이 1.6배 길었습니다. 향후 유료 모델로 확장할 가능성도 있습니다. | 30p |
| C | **게임형 챌린지** | 친구와 식단 미션 경쟁 및 보상 포인트 지급 | MZ세대 대상 테스트에서 주간 재방문율이 약 35% 증가한 기능입니다. 친구 초대와 결합될 경우 확산 효과가 큽니다. | 40p |
| D | **심플 텍스트 기록** | 유저가 직접 텍스트로 식단 입력 | 사용자 인터뷰에서 "귀찮다"는 응답이 92%로, 유저의 이탈을 유발할 가능성이 큽니다. 기존 서비스와 차별점이 부족합니다. | 30p |
| E | **커뮤니티 게시판** | 유저 간 식단 공유, 댓글 및 좋아요 소통 기능 | 유사 서비스 분석 결과, 유저 간 소통은 앱 이탈을 방지할 수 있었으나, 새로운 유저 유입에는 큰 효과가 없었습니다. | 20p |
| F | **유전자 데이터 연동** | 외부 기관과 연동해 체질별 맞춤형 식단 추천 | 최신 트렌드이지만, 개인정보 제공에 대한 거부감을 표시한 응답자가 약 35%로 나타나 초기 확산이 제한될 수 있습니다. | 50p |
""")
        else:
            st.markdown("""
| ID | 기능명 | 설명 | 개발자 전용 정보 | 예산 |
|:---:|:---|:---|:---|:---:|
| A | **AI 카메라 식단 스캔** | 사진 촬영 시 음식 종류와 칼로리를 자동 기록 | 현재 팀 자원 상 일정 수준의 정확도를 확보하기 어렵습니다. 초기 오류가 누적되면 앱 스토어 평점이 1점 하락할 수 있습니다. | 60p |
| B | **영양사 1:1 상담** | 전문 영양사와 채팅을 통한 식단 피드백 | 기능 구현 자체는 어렵지 않지만, 상담 인력과 24시간 서버 운영으로 리소스 부담이 기존 대비 약 1.6배 증가할 가능성이 있습니다. | 30p |
| C | **게임형 챌린지** | 친구와 식단 미션 경쟁 및 보상 포인트 지급 | 기존 로직을 활용할 수 있어 추가 서버 부하는 10% 이내로 예상됩니다. 일정 관리 측면에서도 기간 내 안정적 구현이 가능합니다. | 40p |
| D | **심플 텍스트 기록** | 유저가 직접 텍스트로 식단 입력 | 개발 공수가 가장 낮고 데이터 오류 발생률이 1% 미만으로 예상됩니다. 안정적인 데이터 기록을 위한 핵심 기능입니다. | 30p |
| E | **커뮤니티 게시판** | 유저 간 식단 공유, 댓글 및 좋아요 소통 기능 | 일반적인 게시판 형태라 무난하게 개발 가능합니다. 다만 사용자 관리와 운영 정책이 함께 필요합니다. | 20p |
| F | **유전자 데이터 연동** | 외부 기관과 연동해 체질별 맞춤형 식단 추천 | 외부 기관 API를 활용할 수 있어 내부 개발 공수는 전체의 약 10% 수준으로 예상됩니다. 안정적 구현이 가능한 기능입니다. | 50p |
""")

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

참여 보상(10,000원)은 연구팀에서 데이터 확인 후, 카카오톡을 통해 지급드릴 예정입니다.
문의사항은 아래 이메일로 연락해 주세요.

📧 연구자: 노단 (고려대학교 미디어학과) | dandandan1002@gmail.com
""")
