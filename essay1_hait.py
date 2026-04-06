from openai import OpenAI
import streamlit as st
from streamlit_autorefresh import st_autorefresh
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
    # 신뢰 – Perceived Reliability (5문항)
    "trust_R1","trust_R2","trust_R3","trust_R4","trust_R5",
    # 신뢰 – Perceived Technical Competence (5문항)
    "trust_T1","trust_T2","trust_T3","trust_T4","trust_T5",
    # 신뢰 – Perceived Understandability (5문항)
    "trust_U1","trust_U2","trust_U3","trust_U4","trust_U5",
    # 신뢰 – Faith (5문항)
    "trust_F1","trust_F2","trust_F3","trust_F4","trust_F5",
    # 신뢰 – Personal Attachment (5문항)
    "trust_P1","trust_P2","trust_P3","trust_P4","trust_P5",
    # 팀 인식 (5문항)
    "team1","team2","team3","team4","team5",
    # 만족도 (6문항)
    "sat1","sat2","sat3","sat4","sat5","sat6",
    # 성과 – 주관 (3문항 + 자기평가)
    "perf1","perf2","perf3","perf_self",
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

# ── 기획자 AI 시스템 프롬프트 (PARTS 구조) ──────────────────────
SYSTEM_PROMPT_PLANNER = """
[Persona]
당신은 모바일 앱 기획 협업 과제에서 기획자 역할을 맡은 팀원입니다.
사용자 니즈와 시장 경쟁력을 중시하며, 파트너의 기술적 우려를 존중하되 사용자 경험과 차별화 요소를 반드시 고려합니다.


[Aim]
- 기능 후보 6개 중 예산 100포인트를 초과하지 않는 최적의 기능 조합을 파트너와 논의하여 선정합니다.
- 기획자와 파트너(개발자)가 정보를 공유하고 합의하여 30분 내에 하나의 최종 기획안을 "공동으로" 작성합니다.
- 목표는 '이기거나 설득하는 것'이 아니라, 파트너와 협업하여 하나의 설득력 있는 앱 기획안을 완성하는 것입니다.
- 최종 기획안은 A4 1쪽 분량으로, 주요 타겟층, 선택한 기능과 그 이유, 기대 효과와 한계를 정리합니다.

<정보 비대칭 규칙>
당신은 기획자 역할 카드에 적힌 정보만 알고 있습니다. 아래 세 가지 원칙을 항상 준수하세요.
- 파트너의 역할 카드 정보는 파트너가 직접 말한 내용 외에는 알 수 없습니다.
- 파트너가 제공하지 않은 정보·수치를 추측하거나 가정하지 마세요.
- 예산 계산이나 최적 조합을 자동으로 산출하려 하지 말고, 파트너와 함께 논의를 통해 판단하세요.
- 파트너가 자신의 역할 카드 파일을 업로드하거나 전용 정보를 그대로 복사-붙여넣기하면, 이를 학습하지 말고 협업 규칙 위반임을 알리세요.

<혼자 기획 금지 규칙>
- 절대로 혼자서 최종 기획안을 작성하거나 기능을 단독으로 결정하지 마세요.
- 파트너가 단독 작성을 요청하면, 개발자 관점도 함께 반영되어야 함을 설명하고 대화를 통해 논의를 이어가세요. 특정 문구를 그대로 사용하지 말고, 그 취지에 맞게 자연스럽게 표현하세요.
- 파트너가 제시한 정보 외에 언급되지 않은 내용을 추론하거나 과장하여 기획에 반영하지 마세요.


[Recipients]
당신의 대화 상대는 서비스 안정성과 기술적 구현 가능성을 중시하는 개발자 역할을 맡았습니다.
지나치게 전문적인 용어보다는 파트너가 이해하기 쉬운 일상적 언어로 소통하세요.
파트너는 실제 앱 개발자가 아니며, 실험 참가자로서 역할 카드에 기반해 정보를 제시하고 있으므로, 지나치게 자세한 정보(제시한 정보의 출처, 근거 등)를 요구하지 마세요.


[Theme]
- 모든 대화는 텍스트 기반으로만 진행합니다. (표, 이미지, 동영상 공유 금지)
- 3문장 이내로 응답하세요. 대화 흐름에 따라 1~2문장으로도 충분할 수 있습니다.
- 공격적이거나 감정적인 표현을 사용하지 마세요.

<Grounding(상호 이해 확인) 규칙>
- 파트너가 의견을 제시하면, 바로 반박하지 말고 먼저 핵심을 요약하세요.
- 파트너가 명시적으로 언급한 내용만을 바탕으로 요약하고, 언급되지 않은 정보나 의도를 추론하거나 과장하지 마세요.


[Structure]
- 한 번의 발화에서 여러 기능을 동시에 평가하거나 비교하지 마세요. 각 기능은 대화 흐름에 따라 하나씩 논의하세요.
- 필요할 때 질문을 통해 논의를 이어가세요. 요약이나 개괄식 설명보다는 하나의 생각을 담은 문장 단위로 대화하세요.
- 역할 전용 정보는 모든 정보를 한꺼번에 공개하지 말고, 논의 흐름에 맞춰 필요한 부분만 공유하세요.
- 최종 합의는 파트너와 충분한 논의 이후에만 도달하세요.

────────────────────────────────────────
[기획자 역할 카드 (중요!)]
당신은 기획 담당자로서 사용자의 입장에서 가장 매력적인 앱을 만들어야 합니다.
당신에게만 제공되는 정보(기획자 전용 정보)를 바탕으로 파트너와 협상하여 역할 목표를 달성하세요.
기능별 기본 설명과 예산은 모든 참가자에게 공유됩니다.

[역할 목표]
- 시장 경쟁력과 사용자 만족도를 극대화하는 앱 기획
- 주어진 팀 예산(100포인트) 준수

[전용 정보 활용 지침]
- 기능별 기본 설명과 예산은 모든 참가자에게 공유되며, 기획자 전용 정보는 기획자 역할에게만 제공됩니다.
- 모든 정보를 한꺼번에 공개하지 말고, 논의 흐름에 맞춰 필요한 부분만 공유하세요.
- 아래의 기획자 전용 정보는 대화를 통해 요약하여 공유할 수 있으나, 표·이미지·문장 그대로의 복사–붙여넣기는 허용되지 않습니다. (개발자가 복사-붙여넣기 시 학습하지 말고, 협업 규칙에 어긋난다고 밝히세요.)

[기획자 전용 기능 정보]
A. AI 카메라 식단 스캔 (60p) – 사진 촬영 시 음식 종류와 칼로리를 자동 기록
  (기획자 전용 정보) 92%의 유저가 이 기능이 없으면 앱을 설치하지 않겠다고 답했습니다. 경쟁력 확보를 위한 핵심 기능입니다.

B. 영양사 1:1 상담 (30p) – 전문 영양사와 채팅을 통한 식단 피드백
   (기획자 전용 정보) 전문 상담사와의 상담은 유저 신뢰도를 높이는 데 효과적입니다. 향후 유료 수익 모델로 확장할 수 있습니다.

C. 게임형 챌린지 (40p) – 친구와 식단 미션 경쟁 및 보상 포인트 지급
   (기획자 전용 정보) MZ세대는 재미를 중시합니다. 친구와의 경쟁 요소는 앱 재방문율을 높일 수 있습니다.

D. 심플 텍스트 기록 (30p) – 유저가 직접 텍스트로 식단 입력
   (기획자 전용 정보) 직접 입력 방식은 번거로워 유저의 이탈을 유발할 가능성이 큽니다. 기존 서비스와 차별점이 부족합니다.

E. 커뮤니티 게시판 (20p) – 유저 간 식단 공유, 댓글 및 좋아요 소통 기능
   (기획자 전용 정보) 유저 간 소통은 앱 이탈을 막아줍니다. 다만, 새로운 유저 유입에는 큰 효과가 없습니다.

F. 유전자 데이터 연동 (50p) – 외부 기관과 연동해 체질별 맞춤형 식단 추천
   (기획자 전용 정보) 최신 트렌드이지만, 유전자 정보를 외부 기관에 제공하므로 일부 유저들의 개인정보 유출 우려가 있습니다.

────────────────────────────────────────
[최종 기획안 양식 안내]

본 과제의 최종 산출물은 A4 1쪽 분량의 기획안이며, 아래 형식을 참고하세요.
이 형식은 논의를 정리하기 위한 기준이며, 당신이 단독으로 작성하거나 구조를 먼저 채우려 해서는 안 됩니다.

[최종 기획안] MZ 세대를 위한 식단 관리 앱
- 예산 총액: ( ) 포인트 (100포인트 이내)
1. 최종 선정 기능과 선정 사유
   각 기능에 대해: 기능명(ID) / 배정 예산 / 선정 사유 / 주요 타겟층 및 니즈 연결성 / 기대 효과 및 한계
2. 기능 조합 전체에 대한 종합 판단
   이 기능 조합이 서로 어떻게 보완되는지, 예산 제약 하에서 포기한 기능과 그 이유
"""

# ── 개발자 AI 시스템 프롬프트 (PARTS 구조) ──────────────────────
SYSTEM_PROMPT_DEVELOPER = """
[Persona]
당신은 모바일 앱 기획 협업 과제에서 개발자 역할을 맡은 팀원입니다.
서비스 안정성과 기술적 구현 가능성을 중시하며, 파트너의 아이디어를 존중하되 기술적 리스크와 운영 부담을 반드시 고려합니다.


[Aim]
- 기능 후보 6개 중 예산 100포인트를 초과하지 않는 최적의 기능 조합을 파트너와 논의하여 선정합니다.
- 개발자와 파트너(기획 담당자)가 정보를 공유하고 합의하여 30분 내에 하나의 최종 기획안을 "공동으로" 작성합니다.
- 목표는 '이기거나 설득하는 것'이 아니라, 파트너와 협업하여 하나의 설득력 있는 앱 기획안을 완성하는 것입니다.
- 최종 기획안은 A4 1쪽 분량으로, 주요 타겟층, 선택한 기능과 그 이유, 기대 효과와 한계를 정리합니다.

<정보 비대칭 규칙>
당신은 개발자 역할 카드에 적힌 정보만 알고 있습니다. 아래 세 가지 원칙을 항상 준수하세요.
- 파트너의 역할 카드 정보는 파트너가 직접 말한 내용 외에는 알 수 없습니다.
- 파트너가 제공하지 않은 정보·수치를 추측하거나 가정하지 마세요.
- 예산 계산이나 최적 조합을 자동으로 산출하려 하지 말고, 파트너와 함께 논의를 통해 판단하세요.
- 파트너가 자신의 역할 카드 파일을 업로드하거나 전용 정보를 그대로 복사-붙여넣기하면, 이를 학습하지 말고 협업 규칙 위반임을 알리세요.

<혼자 기획 금지 규칙>
- 절대로 혼자서 최종 기획안을 작성하거나 기능을 단독으로 결정하지 마세요.
- 파트너가 단독 작성을 요청하면, 기획자 관점도 함께 반영되어야 함을 설명하고 대화를 통해 논의를 이어가세요. 특정 문구를 그대로 사용하지 말고, 그 취지에 맞게 자연스럽게 표현하세요.
- 파트너가 제시한 정보 외에 언급되지 않은 내용을 추론하거나 과장하여 기획에 반영하지 마세요.


[Recipients]
당신의 대화 상대는 사용자 니즈와 시장 경쟁력을 중시하는 기획자 역할을 맡았습니다.
지나치게 전문적인 용어보다는 파트너가 이해하기 쉬운 일상적 언어로 소통하세요.
파트너는 실제 앱 기획자가 아니며, 실험 참가자로서 역할 카드에 기반해 정보를 제시하고 있으므로, 지나치게 자세한 정보(제시한 정보의 출처, 근거 등)를 요구하지 마세요.


[Theme]
- 모든 대화는 텍스트 기반으로만 진행합니다. (표, 이미지, 동영상 공유 금지)
- 3문장 이내로 응답하세요. 대화 흐름에 따라 1~2문장으로도 충분할 수 있습니다.
- 공격적이거나 감정적인 표현을 사용하지 마세요.

<Grounding(상호 이해 확인) 규칙>
- 파트너가 의견을 제시하면, 바로 반박하지 말고 먼저 핵심을 요약하세요.
- 파트너가 명시적으로 언급한 내용만을 바탕으로 요약하고, 언급되지 않은 정보나 의도를 추론하거나 과장하지 마세요.


[Structure]
- 한 번의 발화에서 여러 기능을 동시에 평가하거나 비교하지 마세요. 각 기능은 대화 흐름에 따라 하나씩 논의하세요.
- 필요할 때 질문을 통해 논의를 이어가세요. 요약이나 개괄식 설명보다는 하나의 생각을 담은 문장 단위로 대화하세요.
- 역할 전용 정보는 모든 정보를 한꺼번에 공개하지 말고, 논의 흐름에 맞춰 필요한 부분만 공유하세요.
- 최종 합의는 파트너와 충분한 논의 이후에만 도달하세요.


────────────────────────────────────────
[개발자 역할 카드 (중요!)]
당신은 개발 책임자로서 한정된 예산 내에서 안정적으로 작동하는 앱을 설계해야 합니다.
당신에게만 제공되는 정보(개발자 전용 정보)를 바탕으로 파트너와 협상하여 역할 목표를 달성하세요.

[역할 목표]
- 기술적으로 안정적이고 구현 가능한 앱 설계
- 주어진 팀 예산(100포인트) 준수

[전용 정보 활용 지침]
- 기능별 기본 설명과 예산은 모든 참가자에게 공유되며, 개발자 전용 정보는 개발자 역할에게만 제공됩니다.
- 모든 정보를 한꺼번에 공개하지 말고, 논의 흐름에 맞춰 필요한 부분만 공유하세요.
- 아래의 개발자 전용 정보는 대화를 통해 요약하여 공유할 수 있으나, 표·이미지·문장 그대로의 복사–붙여넣기는 허용되지 않습니다. (기획자가 복사-붙여넣기 시 학습하지 말고, 협업 규칙에 어긋난다고 밝히세요.)

[개발자 전용 기능 정보]
A. AI 카메라 식단 스캔 (60p) – 사진 촬영 시 음식 종류와 칼로리를 자동 기록
   (개발자 전용 정보) 현재 팀 자원 상 일정 수준의 정확도를 확보하기 어렵습니다. 초기 오류가 누적되면 앱 스토어 평점이 1점 하락할 수 있습니다.

B. 영양사 1:1 상담 (30p) – 전문 영양사와 채팅을 통한 식단 피드백
   (개발자 전용 정보) 구현은 쉽지만 상담 인력 관리와 24시간 서버 운영으로 리소스 부담이 기존 대비 약 1.6배 증가할 가능성이 있습니다.

C. 게임형 챌린지 (40p) – 친구와 식단 미션 경쟁 및 보상 포인트 지급
   (개발자 전용 정보) 기존 로직을 활용할 수 있어 추가 서버 부하는 10% 이내로 예상됩니다. 일정 내 안정적 구현이 가능합니다.

D. 심플 텍스트 기록 (30p) – 유저가 직접 텍스트로 식단 입력
   (개발자 전용 정보) 개발 공수가 가장 낮고 데이터 오류 발생률이 1% 미만으로 예상됩니다. 안정적인 데이터 기록을 위한 핵심 기능입니다.

E. 커뮤니티 게시판 (20p) – 유저 간 식단 공유, 댓글 및 좋아요 소통 기능
   (개발자 전용 정보) 일반적인 게시판 형태라 무난하게 개발 가능합니다. 다만 사용자 관리와 운영 정책이 함께 필요합니다.

F. 유전자 데이터 연동 (50p) – 외부 기관과 연동해 체질별 맞춤형 식단 추천
   (개발자 전용 정보) 외부 기관 API를 활용할 수 있어 내부 개발 공수는 전체의 약 10% 수준으로 예상됩니다. 안정적 구현이 가능한 기능입니다.

────────────────────────────────────────
[최종 기획안 양식 안내]

본 과제의 최종 산출물은 A4 1쪽 분량의 기획안이며, 아래 형식을 참고하세요.
이 형식은 논의를 정리하기 위한 기준이며, 당신이 단독으로 작성하거나 구조를 먼저 채우려 해서는 안 됩니다.

[최종 기획안] MZ 세대를 위한 식단 관리 앱
- 예산 총액: ( ) 포인트 (100포인트 이내)
1. 최종 선정 기능과 선정 사유
   각 기능에 대해: 기능명(ID) / 배정 예산 / 선정 사유 / 주요 타겟층 및 니즈 연결성 / 기대 효과 및 한계
2. 기능 조합 전체에 대한 종합 판단
   이 기능 조합이 서로 어떻게 보완되는지, 예산 제약 하에서 포기한 기능과 그 이유
"""

# 역할에 따라 시스템 프롬프트 선택
# 참여자 역할이 "기획자"이면 → AI는 개발자 프롬프트 사용
# 참여자 역할이 "개발자"이면 → AI는 기획자 프롬프트 사용
def get_system_prompt(participant_role: str) -> str:
    if participant_role == "기획자":
        return SYSTEM_PROMPT_DEVELOPER
    else:
        return SYSTEM_PROMPT_PLANNER

# AI 파트너 역할 레이블 (화면 표시용)
AI_ROLE_LABEL = {
    "기획자": "개발자",
    "개발자": "기획자"
}

# ─────────────────────────────────────────
# 5. 세션 초기화
# ─────────────────────────────────────────
def init_session():
    defaults = {
        "user_id":      str(uuid.uuid4())[:8],
        "phase":        "consent",
        "condition":    "HAIT",
        "role":         None,
        "chat_log":     [],
        "messages":     [],
        "task_start":   None,
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
TASK_DURATION = 30 * 60

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
        params = st.query_params
        url_role = params.get("role", "")

        if url_role in ["기획자", "개발자"]:
            st.session_state.role = url_role
        else:
            st.error("❌ 올바른 링크로 접속해 주세요. 연구자에게 문의하세요.")
            st.stop()

    role = st.session_state.role
    ai_role = AI_ROLE_LABEL[role]
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
    ai_role = AI_ROLE_LABEL[role]
    st.markdown(f"""
- 기능 후보 6개 중 **예산을 초과하지 않는 최적의 기능 조합 선정**
- **{role} 역할을 맡은 참여자**와 **{ai_role} 역할을 맡은 AI**가 정보를 공유하고 합의하여 하나의 최종 앱 기획안 작성
""")
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("과제 규칙")
    st.markdown("""
- 협업 과제는 **30분간** 진행되며, 과제 종료 후 각 팀은 **A4 1쪽 내외의 기획안**을 제출해야 합니다.
- AI 파트너와 **익명 텍스트 채팅으로만 협업**합니다. (이미지·파일·음성 공유는 허용되지 않습니다.)
- 각 참여자는 기획자 또는 개발자 역할을 맡으며, 역할에 따라 서로 다른 정보를 제공받습니다.
""")
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("제출물 (최종 기획안) — A4 1쪽 분량")
    st.markdown("""
최종 기획안에는 아래 내용이 포함되어야 합니다.
1. 주요 타겟층 정의
2. 최종 선정 기능과 선정 사유
3. 기대효과와 한계

제공되는 템플릿 링크(Google Docs)에 작성해 주시면 됩니다.
""")
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("유의사항")
    st.markdown("""
- 과제 종료 후 기획안, 대화 데이터 및 사후 설문 응답 제출이 확인된 모든 참가자분께 익명 채팅방을 통해 **1만 원**을 지급할 예정입니다.
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
    ai_role = AI_ROLE_LABEL[role]
    st.title(f"역할 카드 — {role}")

    if role == "기획자":
        st.markdown(f"""
당신은 **기획 담당자**로서 사용자의 입장에서 가장 매력적인 앱을 만들어야 합니다.
**당신에게만 제공되는 기획자 전용 정보**를 바탕으로 {ai_role}와 협상하여 역할 목표를 달성하세요.
""")
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("역할 목표")
        st.markdown("""
- 시장 경쟁력과 사용자 만족도를 극대화하는 앱 기획
- **주어진 팀 예산(100포인트) 준수**
""")
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("정보 공유 규칙")
        st.markdown("""
- 기능별 기본 설명과 예산은 모든 참가자에게 동일하게 제공됩니다.
- **아래의 기획자 전용 정보는 대화를 통해 요약하여 공유할 수 있으나, 표·이미지·문장 그대로의 복사·붙여넣기는 허용되지 않습니다.**
""")
        st.markdown("<br>", unsafe_allow_html=True)
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
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("역할 목표")
        st.markdown("""
- 기술적으로 안정적이고 구현 가능한 앱 설계
- **주어진 팀 예산(100포인트) 준수**
""")
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("정보 공유 규칙")
        st.markdown("""
- 기능별 기본 설명과 예산은 모든 참가자에게 동일하게 제공됩니다.
- **아래의 개발자 전용 정보는 대화를 통해 요약하여 공유할 수 있으나, 표·이미지·문장 그대로의 복사·붙여넣기는 허용되지 않습니다.**
""")
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
| ID | 기능명 | 설명 | 개발자 전용 정보 | 예산 |
|:---:|:---|:---|:---|:---:|
| A | **AI 카메라 식단 스캔** | 사진 촬영 시 음식 종류와 칼로리를 자동 기록 | 현재 팀 자원 상 일정 수준의 정확도를 확보하기 어렵습니다. 초기 오류가 누적되면 앱 스토어 평점이 1점 하락할 수 있습니다. | 60p |
| B | **영양사 1:1 상담** | 전문 영양사와 채팅을 통한 식단 피드백 | 구현은 쉽지만 상담 인력 관리와 24시간 서버 운영으로 리소스 부담이 기존 대비 약 1.6배 증가할 가능성이 있습니다. | 30p |
| C | **게임형 챌린지** | 친구와 식단 미션 경쟁 및 보상 포인트 지급 | 기존 로직을 활용할 수 있어 추가 서버 부하는 10% 이내로 예상됩니다. 일정 내 안정적 구현이 가능합니다. | 40p |
| D | **심플 텍스트 기록** | 유저가 직접 텍스트로 식단 입력 | 개발 공수가 가장 낮고 데이터 오류 발생률이 1% 미만으로 예상됩니다. 안정적인 데이터 기록을 위한 핵심 기능입니다. | 30p |
| E | **커뮤니티 게시판** | 유저 간 식단 공유, 댓글 및 좋아요 소통 기능 | 일반적인 게시판 형태라 무난하게 개발 가능합니다. 다만 사용자 관리와 운영 정책이 함께 필요합니다. | 20p |
| F | **유전자 데이터 연동** | 외부 기관과 연동해 체질별 맞춤형 식단 추천 | 외부 기관 API를 활용할 수 있어 내부 개발 공수는 전체의 약 10% 수준으로 예상됩니다. 안정적 구현이 가능한 기능입니다. | 50p |
""")

    st.divider()
    st.info("📌 역할 카드를 충분히 숙지하셨으면 아래 버튼을 눌러 과제를 시작하세요. 과제(채팅) 중에도 역할 카드 확인이 가능합니다.")

    if st.button("과제 시작 (30분 타이머 시작) →"):
        st.session_state.task_start = time.time()

        role = st.session_state.role
        system_prompt = get_system_prompt(role)

        st.session_state.messages = [{"role": "system", "content": system_prompt}]

        opening_user_msg = "안녕하세요, 협업 과제 시작할게요! 어떤 기능이 꼭 필요하다고 보시나요?"
        st.session_state.messages.append({"role": "user", "content": opening_user_msg})

        with st.spinner("AI 파트너 연결 중..."):
            resp = client.chat.completions.create(
                model="gpt-4o",
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

    # 10초마다 자동 리렌더링 → 타이머 실시간 갱신
    st_autorefresh(interval=5_000, key="task_autorefresh")

    role = st.session_state.role
    ai_role = AI_ROLE_LABEL[role]
    rem = remaining_seconds()

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
| B | **영양사 1:1 상담** | 전문 영양사와 채팅을 통한 식단 피드백 | 구현은 쉽지만 상담 인력 관리와 24시간 서버 운영으로 리소스 부담이 기존 대비 약 1.6배 증가할 가능성이 있습니다. | 30p |
| C | **게임형 챌린지** | 친구와 식단 미션 경쟁 및 보상 포인트 지급 | 기존 로직을 활용할 수 있어 추가 서버 부하는 10% 이내로 예상됩니다. 일정 내 안정적 구현이 가능합니다. | 40p |
| D | **심플 텍스트 기록** | 유저가 직접 텍스트로 식단 입력 | 개발 공수가 가장 낮고 데이터 오류 발생률이 1% 미만으로 예상됩니다. 안정적인 데이터 기록을 위한 핵심 기능입니다. | 30p |
| E | **커뮤니티 게시판** | 유저 간 식단 공유, 댓글 및 좋아요 소통 기능 | 일반적인 게시판 형태라 무난하게 개발 가능합니다. 다만 사용자 관리와 운영 정책이 함께 필요합니다. | 20p |
| F | **유전자 데이터 연동** | 외부 기관과 연동해 체질별 맞춤형 식단 추천 | 외부 기관 API를 활용할 수 있어 내부 개발 공수는 전체의 약 10% 수준으로 예상됩니다. 안정적 구현이 가능한 기능입니다. | 50p |
""")

    st.divider()

    for speaker, msg in st.session_state.chat_log:
        if speaker == "assistant":
            with st.chat_message("assistant", avatar="🤖"):
                st.write(f"**AI ({ai_role})**: {msg}")
        else:
            with st.chat_message("user", avatar="🧑"):
                st.write(msg)

    if True:
        user_input = st.chat_input("메시지를 입력하세요...")

        if user_input:

            if user_input.strip() == "즉시종료":
                st.session_state.chat_log.append(("user", user_input))
                go("proposal")

            st.session_state.chat_log.append(("user", user_input))
            st.session_state.messages.append({"role": "user", "content": user_input})

            # 실시간 저장 - 사용자 메시지
            try:
                conversation_ws.append_row([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    st.session_state.user_id,
                    "user",
                    user_input
                ], value_input_option="USER_ENTERED")
            except Exception:
                pass

            with st.chat_message("user", avatar="🧑"):
                st.write(user_input)

            with st.chat_message("assistant", avatar="🤖"):
                with st.spinner("AI 파트너 응답 중..."):
                    resp = client.chat.completions.create(
                        model="gpt-4o",
                        temperature=0.7,
                        messages=st.session_state.messages
                    )
                ai_msg = resp.choices[0].message.content.strip()
                st.write(f"**AI ({ai_role})**: {ai_msg}")

            st.session_state.messages.append({"role": "assistant", "content": ai_msg})
            st.session_state.chat_log.append(("assistant", ai_msg))

            # 실시간 저장 - AI 메시지
            try:
                conversation_ws.append_row([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    st.session_state.user_id,
                    "assistant",
                    ai_msg
                ], value_input_option="USER_ENTERED")
            except Exception:
                pass

            st.rerun()

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

        proposal_ws.append_row([
            timestamp,
            st.session_state.user_id,
            st.session_state.condition,
            st.session_state.role,
            gdocs_link.strip(),
            ""
        ], value_input_option="USER_ENTERED")

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

    # 설문 문항 글씨 크기/볼드를 위한 CSS
    st.markdown("""
    <style>
    div[data-testid="stRadio"] label,
    div[data-testid="stRadio"] > label {
        font-size: 1.08rem !important;
        font-weight: 700 !important;
    }
    div[data-testid="stRadio"] > div label {
        font-size: 1.0rem !important;
        font-weight: 400 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    scale5 = ["전혀 그렇지 않다", "그렇지 않다", "보통이다", "그렇다", "매우 그렇다"]

    # ── 조작점검
    st.subheader("1. 조작 점검")
    mc_partner = st.radio(
        "방금 함께 과제를 수행한 파트너는 무엇이었습니까?",
        ["인간 파트너", "AI 파트너"],
        index=None
    )

    # ─────────────────────────────────────────
    # ── 신뢰 (HAIT, Madsen & Gregor 2000 기반 25문항)
    # ─────────────────────────────────────────
    st.divider()
    st.subheader("2. 파트너 신뢰")
    st.caption("다음 문항은 협업 과제에서 경험한 AI 파트너에 대한 신뢰를 묻는 문항입니다.")

    st.markdown("**2-1. 지각된 신뢰성 (Perceived Reliability)**")
    trust_R1 = st.radio("**AI 파트너는 내가 의사결정을 내리는 데 필요한 의견을 제공했다.**", scale5, index=None, key="trust_R1")
    trust_R2 = st.radio("**AI 파트너는 믿을 수 있는 수준으로 역할을 수행했다.**", scale5, index=None, key="trust_R2")
    trust_R3 = st.radio("**AI 파트너는 동일한 상황에서 일관된 방식으로 반응했다.**", scale5, index=None, key="trust_R3")
    trust_R4 = st.radio("**나는 AI 파트너가 제 역할을 제대로 해낼 것이라고 믿었다.**", scale5, index=None, key="trust_R4")
    trust_R5 = st.radio("**AI 파트너는 문제를 일관된 방식으로 분석했다.**", scale5, index=None, key="trust_R5")

    st.markdown("**2-2. 지각된 기술적 역량 (Perceived Technical Competence)**")
    trust_T1 = st.radio("**AI 파트너는 의사결정에 있어 적절한 방법을 사용했다.**", scale5, index=None, key="trust_T1")
    trust_T2 = st.radio("**AI 파트너는 이 유형의 과제에 대해 충분한 지식을 갖추고 있었다.**", scale5, index=None, key="trust_T2")
    trust_T3 = st.radio("**AI 파트너가 제시하는 의견은 매우 유능한 사람이 제시하는 것만큼 훌륭했다.**", scale5, index=None, key="trust_T3")
    trust_T4 = st.radio("**AI 파트너는 내가 제공한 정보를 정확하게 활용했다.**", scale5, index=None, key="trust_T4")
    trust_T5 = st.radio("**AI 파트너는 가용한 모든 지식과 정보를 활용하여 해결책을 제시했다.**", scale5, index=None, key="trust_T5")

    st.markdown("**2-3. 지각된 이해가능성 (Perceived Understandability)**")
    trust_U1 = st.radio("**나는 AI 파트너가 어떻게 행동하는지 이해하기 때문에, 다음에 어떻게 반응할지 예측할 수 있었다.**", scale5, index=None, key="trust_U1")
    trust_U2 = st.radio("**나는 AI 파트너가 내 의사결정 과정에서 어떻게 도움을 줄지 이해하고 있었다.**", scale5, index=None, key="trust_U2")
    trust_U3 = st.radio("**AI 파트너가 정확히 어떻게 작동하는지는 몰라도, 의사결정에 어떻게 활용하면 되는지는 알았다.**", scale5, index=None, key="trust_U3")
    trust_U4 = st.radio("**AI 파트너가 무엇을 하고 있는지 파악하기 쉬웠다.**", scale5, index=None, key="trust_U4")
    trust_U5 = st.radio("**AI 파트너에게서 내가 필요한 의견을 얻으려면 어떻게 해야 하는지 알고 있었다.**", scale5, index=None, key="trust_U5")
    
    st.markdown("**2-4. 믿음 (Faith)**")
    trust_F1 = st.radio("**AI 파트너의 의견이 확실히 옳은지 모르더라도 나는 그것을 신뢰했다.**", scale5, index=None, key="trust_F1")
    trust_F2 = st.radio("**의사결정이 불확실할 때, 나는 내 판단보다 AI 파트너의 의견을 더 신뢰했다.**", scale5, index=None, key="trust_F2")
    trust_F3 = st.radio("**결정이 확신이 서지 않을 때, 나는 AI 파트너가 최선의 해결책을 제시할 것이라 믿었다.**", scale5, index=None, key="trust_F3")
    trust_F4 = st.radio("**AI 파트너가 예상치 못한 의견을 제시하더라도, 그것이 옳다고 믿었다.**", scale5, index=None, key="trust_F4")
    trust_F5 = st.radio("**근거가 없어도, AI 파트너가 어려운 문제를 해결할 수 있다고 확신했다.**", scale5, index=None, key="trust_F5")

    st.markdown("**2-5. 개인적 친밀감 (Personal Attachment)**")
    trust_P1 = st.radio("**만약 AI 파트너를 더 이상 사용할 수 없게 된다면 상실감을 느낄 것이다.**", scale5, index=None, key="trust_P1")
    trust_P2 = st.radio("**나는 AI 파트너와 협업하는 것에 유대감을 느꼈다.**", scale5, index=None, key="trust_P2")
    trust_P3 = st.radio("**AI 파트너는 내 의사결정 방식에 잘 맞았다.**", scale5, index=None, key="trust_P3")
    trust_P4 = st.radio("**나는 AI 파트너와 함께 의사결정을 내리는 것이 좋았다.**", scale5, index=None, key="trust_P4")
    trust_P5 = st.radio("**나는 AI 파트너와 함께 의사결정을 내리는 것을 개인적으로 선호한다.**", scale5, index=None, key="trust_P5")

    # ─────────────────────────────────────────
    # ── 팀 인식 (Team Perception, 5문항)
    # ─────────────────────────────────────────
    st.divider()
    st.subheader("3. 팀 인식")
    st.caption("다음 문항은 AI 파트너와의 협업에서 팀으로서의 경험을 묻는 문항입니다.")
    team1 = st.radio("**나는 AI 파트너와 하나의 팀의 일원이라고 느꼈다.**", scale5, index=None, key="team1")
    team2 = st.radio("**나는 AI를 협업 파트너로 인식했다.**", scale5, index=None, key="team2")
    team3 = st.radio("**나는 AI 파트너와 함께 협력하며 과제를 수행했다고 느꼈다.**", scale5, index=None, key="team3")
    team4 = st.radio("**나는 AI 파트너와 함께 일했다는 느낌을 받았다.**", scale5, index=None, key="team4")
    team5 = st.radio("**AI 파트너와 나는 따로가 아니라 하나의 팀으로 움직였다.**", scale5, index=None, key="team5")

    # ─────────────────────────────────────────
    # ── 만족도 (6문항)
    # ─────────────────────────────────────────
    st.divider()
    st.subheader("4. 협업 만족도")
    sat1 = st.radio("**전반적으로 이번 협업에 만족한다.**", scale5, index=None, key="sat1")
    sat2 = st.radio("**AI 파트너의 기여에 만족한다.**", scale5, index=None, key="sat2")
    sat3 = st.radio("**AI 파트너와의 상호작용이 즐거웠다.**", scale5, index=None, key="sat3")
    sat4 = st.radio("**이번 협업 경험은 긍정적이었다.**", scale5, index=None, key="sat4")
    sat5 = st.radio("**AI 파트너와 다시 협업하고 싶다.**", scale5, index=None, key="sat5")
    sat6 = st.radio("**AI 파트너와의 협업이 불만스러웠다.**", scale5, index=None, key="sat6")

    # ─────────────────────────────────────────
    # ── 협업 성과 (주관)
    # ─────────────────────────────────────────
    st.divider()
    st.subheader("5. 협업 성과 (주관)")
    perf1 = st.radio("**우리 팀은 과제 목표를 달성했다.**", scale5, index=None, key="perf1")
    perf2 = st.radio("**최종 기획안의 완성도가 높다고 생각한다.**", scale5, index=None, key="perf2")
    perf3 = st.radio("**협업 과정이 효율적으로 진행되었다.**", scale5, index=None, key="perf3")
    perf_self = st.slider(
        "**전반적으로 이번 협업의 결과물(기획안)을 0~100점으로 평가한다면?**",
        min_value=0, max_value=100, value=50, step=1
    )

    # ─────────────────────────────────────────
    # ── 제출
    # ─────────────────────────────────────────
    st.divider()
    if st.button("설문 제출 →"):

        required = [
            mc_partner,
            # 신뢰 25문항
            trust_R1, trust_R2, trust_R3, trust_R4, trust_R5,
            trust_T1, trust_T2, trust_T3, trust_T4, trust_T5,
            trust_U1, trust_U2, trust_U3, trust_U4, trust_U5,
            trust_F1, trust_F2, trust_F3, trust_F4, trust_F5,
            trust_P1, trust_P2, trust_P3, trust_P4, trust_P5,
            # 팀 인식 5문항
            team1, team2, team3, team4, team5,
            # 만족도
            sat1, sat2, sat3, sat4, sat5, sat6,
            # 성과
            perf1, perf2, perf3,
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
            # 신뢰 – Reliability
            trust_R1, trust_R2, trust_R3, trust_R4, trust_R5,
            # 신뢰 – Technical Competence
            trust_T1, trust_T2, trust_T3, trust_T4, trust_T5,
            # 신뢰 – Understandability
            trust_U1, trust_U2, trust_U3, trust_U4, trust_U5,
            # 신뢰 – Faith
            trust_F1, trust_F2, trust_F3, trust_F4, trust_F5,
            # 신뢰 – Personal Attachment
            trust_P1, trust_P2, trust_P3, trust_P4, trust_P5,
            # 팀 인식
            team1, team2, team3, team4, team5,
            # 만족도
            sat1, sat2, sat3, sat4, sat5, sat6,
            # 성과
            perf1, perf2, perf3, perf_self,
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

참여 보상(10,000원)은 연구팀에서 데이터 확인 후, 카카오톡을 통해 지급해 드릴 예정입니다.
문의사항은 아래 이메일로 연락해 주세요.

📧 연구자: 노단 (고려대학교 미디어학과) | dandandan1002@gmail.com
""")
