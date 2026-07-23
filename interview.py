# Main application for running AI-led democracy interviews
#
# AI interviewer: text
# Respondent: text


import os
import time
from copy import deepcopy

import streamlit as st
from openai import OpenAI

import config
from utils import (
    check_password,
    stream_response,
    save_backup,
    load_backup,
    save_transcript_and_metadata,
    is_transcript_saved,
)


#
# 1. Set up interview environment
#

st.set_page_config(page_title="Interview", page_icon=config.AVATAR_INTERVIEWER)

st.markdown(
    """
    <style>
    .block-container {
        padding-bottom: 9rem;
    }
    .section-end-spacer {
        height: 7rem;
    }
    .summary-page-header {
        border-bottom: 1px solid #e5e7eb;
        margin-bottom: 1.25rem;
        padding-bottom: 1rem;
    }
    .summary-page-eyebrow {
        color: #6b7280;
        font-size: 0.9rem;
        margin-bottom: 0.25rem;
    }
    .summary-page-title {
        color: #111827;
        font-size: 1.65rem;
        font-weight: 700;
        line-height: 1.2;
        margin-bottom: 0.35rem;
    }
    .summary-page-subtitle {
        color: #4b5563;
        font-size: 0.98rem;
        line-height: 1.45;
        max-width: 44rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


CONSENT_FORM = """## Consent form

**This is a survey conducted for academic research purposes by researchers from Stanford University. It will take approximately 20 minutes to complete. The survey data is used for research purposes only, and the research is non-partisan.**

**Part of the survey involves a short conversational interview conducted with a generative artificial intelligence (AI) tool, which will ask you to share your views on a few topics in your own words. You will be compensated for this survey if you complete the survey and your responses pass our survey quality checks. These checks use statistical control methods to detect incoherent and rushed responses. It is very important for the validity of our research that you answer honestly and read the questions carefully before answering.**

**All the answers you provide will remain anonymous and be treated with absolute confidentiality. The personal data we collect will be transferred and stored on secure servers. Only researchers working on the project will have access to the anonymized data. Your participation in this survey is completely voluntary. You are entitled to choose not to take part. If at first you agree to take part, you can later change your mind. Your decision will not be held against you in any way. Your refusal to participate will not result in any consequences or any loss of benefits that you are otherwise entitled to receive. You can ask any questions before you decide whether to participate.**

**Do you agree to participate in the survey?**

***By answering Yes to this question, you confirm that you are 18 years old or over, you have read and understood the explanations above, and you agree to take part in the survey.***"""


if config.LOGINS:
    pwd_correct, username = check_password()
    if not pwd_correct:
        st.stop()
    st.session_state.username = username
else:
    if "username" not in st.session_state:
        st.markdown(CONSENT_FORM)

        with st.form("start_interview_form"):
            username_input = st.text_input(
                "Please enter a username to start the interview:",
                key="username_input",
            )
            consent_response = st.radio(
                "Your response:",
                ["Yes", "No"],
                index=None,
                key="consent_response",
                horizontal=True,
            )
            submitted = st.form_submit_button("Start survey")

        if not submitted:
            st.stop()

        if not username_input:
            st.warning("Please enter a username before continuing.")
            st.stop()

        if not username_input.strip().isalnum():
            st.warning("Username must be alphanumeric.")
            st.stop()

        if consent_response is None:
            st.warning("Please select whether you agree to participate.")
            st.stop()

        if consent_response != "Yes":
            st.info(
                "Thank you. You have not agreed to participate, so the survey will not begin."
            )
            st.stop()

        st.session_state.username = username_input.strip()
        st.session_state.consent_given = True
        st.rerun()


if not os.path.exists(config.TRANSCRIPTS_DIRECTORY):
    os.makedirs(config.TRANSCRIPTS_DIRECTORY)
if not os.path.exists(config.METADATA_DIRECTORY):
    os.makedirs(config.METADATA_DIRECTORY)
if not os.path.exists(config.BACKUPS_DIRECTORY):
    os.makedirs(config.BACKUPS_DIRECTORY)


# For the funder demo, allow a username to be reused instead of locking the user
# out after a completed test run.
allow_repeat_demo_interviews = getattr(config, "ALLOW_REPEAT_DEMO_INTERVIEWS", True)
interview_previously_completed = is_transcript_saved(config.TRANSCRIPTS_DIRECTORY)

if (
    "messages" not in st.session_state
    and interview_previously_completed
    and not allow_repeat_demo_interviews
):
    st.session_state.messages = []
    st.session_state.interview_active = False

    code_found = False
    try:
        messages, _, _, _ = load_backup(backups_directory=config.BACKUPS_DIRECTORY)
    except Exception:
        messages = []

    for code in config.CLOSING_MESSAGES.keys():
        if any(
            code in message["content"]
            for message in messages
            if message["role"] == "assistant"
        ):
            code_found = True
            closing_message = config.CLOSING_MESSAGES[code]
            break

    if not code_found:
        closing_message = "The interview has already been completed."

    st.session_state.interview_active = False
    st.markdown(closing_message)
    st.stop()


if "interview_active" not in st.session_state:
    st.session_state.interview_active = True

if "start_time_current_login" not in st.session_state:
    st.session_state.start_time_current_login = time.time()

if "messages" not in st.session_state:
    if allow_repeat_demo_interviews:
        st.session_state.messages = []
        st.session_state.start_time = time.time()
        st.session_state.times_previous_attempts = []
        st.session_state.num_text_and_voice_answers = [0, 0]
    else:
        (
            st.session_state.messages,
            st.session_state.start_time,
            st.session_state.times_previous_attempts,
            st.session_state.num_text_and_voice_answers,
        ) = load_backup(backups_directory=config.BACKUPS_DIRECTORY)


if isinstance(config.ADDITIONAL_API_KWARGS, dict):
    api_kwargs = deepcopy(config.ADDITIONAL_API_KWARGS)
else:
    raise ValueError(
        "ADDITIONAL_API_KWARGS must be specified as a dictionary in config.py."
    )

api_kwargs["model"] = config.MODEL

if config.API == "openai":
    client = OpenAI(api_key=st.secrets["KEY_OPENAI"])
    api_kwargs["stream"] = True

elif config.API == "anthropic":
    import anthropic

    client = anthropic.Anthropic(api_key=st.secrets["KEY_ANTHROPIC"])
    api_kwargs["system"] = config.SYSTEM_PROMPT
    api_kwargs.setdefault("max_tokens", 4096)

elif config.API == "google":
    from google import genai

    client = genai.Client(api_key=st.secrets["KEY_GOOGLE"])
    api_kwargs.setdefault("config", {})["system_instruction"] = config.SYSTEM_PROMPT

elif config.API == "azure":
    from azure.ai.inference import ChatCompletionsClient
    from azure.core.credentials import AzureKeyCredential

    client = ChatCompletionsClient(
        endpoint=st.secrets["ENDPOINT_AZURE"],
        credential=AzureKeyCredential(st.secrets["KEY_AZURE"]),
        api_version=st.secrets["VERSION_AZURE"],
    )
    api_kwargs["stream"] = True

else:
    raise ValueError(f"Unknown API: {config.API}")


#
# 2. Democracy interview configuration
#

INTRO_MESSAGE = (
    "Hello! I'm glad to have the opportunity to speak with you today about democracy and political life. "
    "Thank you for taking part in this interview. I'm interested in your views in your own words."
)

FINAL_OPEN_QUESTION = (
    "Thinking back over our conversation, is there anything important about your views on democracy "
    "that you feel we have not covered, or that you would like to add?"
)

FINAL_OPEN_MESSAGE = (
    "Thanks for working through those questions. We're nearing the end of the interview. "
    "Before I summarize our conversation, I have one final question:\n\n"
    f"{FINAL_OPEN_QUESTION}"
)

MAX_FOLLOWUPS_PER_SECTION = getattr(config, "MAX_FOLLOWUPS_PER_SECTION", 2)
CONVERSATIONAL_PAUSE_SECONDS = getattr(config, "CONVERSATIONAL_PAUSE_SECONDS", 0.7)

SECTIONS = [
    {
        "id": "meaning",
        "title": "What Democracy Means",
        "question": "What does democracy mean to you?",
        "type": "text",
        "max_followups": 2,
    },
    {
        "id": "importance",
        "title": "How Much Democracy Matters",
        "question": (
            "How important is it for you to live in a country that is governed democratically? "
            "On this scale where 0 means it is not at all important and 10 means absolutely important, "
            "what position would you choose?"
        ),
        "type": "slider",
        "answer_suffix": " out of 10",
        "max_followups": 2,
        "transition": (
            "That helps me understand how you are thinking about democracy as an idea. "
            "I would like to turn now to how much that matters to you personally."
        ),
    },
    {
        "id": "satisfaction",
        "title": "How Democracy Is Working",
        "question": (
            "On the whole, are you very satisfied, fairly satisfied, not very satisfied, "
            "or not at all satisfied with the way democracy works in your country?"
        ),
        "type": "radio",
        "options": [
            "Very satisfied",
            "Fairly satisfied",
            "Not very satisfied",
            "Not at all satisfied",
        ],
        "max_followups": 2,
        "transition": (
            "Thanks. I would like to shift from democracy as a value to how democracy feels "
            "in practice where you live."
        ),
    },
    {
        "id": "satisfaction_drivers",
        "title": "What Shapes Your View",
        "question": (
            "Which areas most shaped your answer about how democracy works in your country? "
            "Feel free to choose among the items listed below, or you can type a response in the text box."
        ),
        "type": "checkboxes_text",
        "options": [
            "Economy / jobs / inflation",
            "Crime and public safety",
            "Corruption",
            "Infrastructure",
            "Public services",
            "Inequality",
            "Rights and freedoms",
            "Political representation",
            "Trust in institutions",
        ],
        "text_label": "Other areas or details you had in mind:",
        "max_followups": 3,
        "transition": (
            "That gives me the broad picture. I would like to get a little more concrete about "
            "what shaped that view."
        ),
    },
    {
        "id": "satisfaction_time",
        "title": "Whether Your View Has Changed",
        "question": (
            "Has your satisfaction with the way democracy works in your country changed over time, "
            "or has it been fairly stable? Please describe what has shaped this view."
        ),
        "type": "text",
        "max_followups": 2,
        "transition": (
            "That is helpful. I am also interested in whether this is a newer view for you, "
            "or something that has been fairly steady."
        ),
    },
    {
        "id": "effectiveness",
        "title": "Democracy And Delivery",
        "question": (
            "What is more important to you: that a government be democratic even if it is not "
            "effective, or that it be effective even if it is not democratic?"
        ),
        "type": "radio",
        "options": [
            "That the government be democratic even if it is not effective",
            "That the government be effective even if it is not democratic",
        ],
        "max_followups": 3,
        "transition": (
            "Thanks for explaining that. I want to ask about a tradeoff people sometimes feel "
            "between democratic process and getting things done."
        ),
    },
    {
        "id": "regime_preference",
        "title": "Democracy And Alternatives",
        "question": (
            "Which of the following statements do you agree with most? Choose one of the three options."
        ),
        "type": "radio",
        "options": [
            "Democracy is preferable to any other form of government.",
            "In some circumstances, an authoritarian government may be preferable to a democratic one.",
            "For people like me, it makes no difference whether the regime is democratic or not.",
        ],
        "max_followups": 3,
        "transition": (
            "That helps clarify how you think about process and results. I would like to ask more "
            "directly how you think about democracy compared with other forms of government."
        ),
    },
    {
        "id": "red_lines",
        "title": "Democratic Boundaries",
        "question": (
            "Are there any things democratic leaders should not do, even if they promise better results? "
            "Feel free to choose among the items listed below, or type your own answer."
        ),
        "type": "checkboxes_text",
        "options": [
            "Cancel or ignore elections",
            "Stop opposition parties from competing",
            "Pressure or control the courts",
            "Restrict the media",
            "Limit people's right to criticize the government",
            "Ignore legal limits on their power",
            "Use force against peaceful opponents",
            "Refuse to leave office after losing an election",
            "None of these are absolute",
        ],
        "exclusive_options": ["None of these are absolute"],
        "text_label": "Other things democratic leaders should not do:",
        "max_followups": 3,
        "transition": (
            "Thanks. For the last main part of the interview, I would like to ask where you draw "
            "the line for democratic leaders."
        ),
    },
]


def followups_required(section):
    return section.get("max_followups", MAX_FOLLOWUPS_PER_SECTION)


def get_section(section_id):
    return next(section for section in SECTIONS if section["id"] == section_id)


def section_question_text(section):
    return section.get("transcript_question", section["question"])


def section_start_index(section):
    question_text = section_question_text(section)
    for index, message in enumerate(st.session_state.messages):
        if message["role"] == "assistant" and message["content"] == question_text:
            return index
    return None


def next_section_start_index(section):
    start_index = section_start_index(section)
    if start_index is None:
        return None

    later_starts = []
    for later_section in SECTIONS:
        later_start = section_start_index(later_section)
        if later_start is not None and later_start > start_index:
            later_starts.append(later_start)

    if later_starts:
        return min(later_starts)

    final_question_index = final_open_question_index()
    if final_question_index is not None and final_question_index > start_index:
        return final_question_index

    return len(st.session_state.messages)


def section_messages(section):
    start_index = section_start_index(section)
    end_index = next_section_start_index(section)

    if start_index is None or end_index is None:
        return []

    # Hide the app-managed survey item and anchor answer from the
    # respondent-facing chat; keep the conversational follow-ups.
    return st.session_state.messages[start_index + 2 : end_index]


def closed_answer(section):
    start_index = section_start_index(section)
    if start_index is None or len(st.session_state.messages) <= start_index + 1:
        return None

    return st.session_state.messages[start_index + 1]["content"]


def followup_answer_count(section):
    return sum(
        1 for message in section_messages(section) if message["role"] == "user"
    )


def all_section_followups_complete():
    return all(
        section_start_index(section) is not None
        and followup_answer_count(section) >= followups_required(section)
        for section in SECTIONS
    )


def summary_has_been_asked():
    return any(
        message["role"] == "assistant"
        and "how well does the summary of our discussion describe your views about democracy" in message["content"].lower()
        for message in st.session_state.messages
    )


def final_open_question_index():
    for index, message in enumerate(st.session_state.messages):
        if (
            message["role"] == "assistant"
            and FINAL_OPEN_QUESTION in message["content"]
        ):
            return index
    return None


def final_open_question_has_been_answered():
    question_index = final_open_question_index()
    if question_index is None:
        return False

    return any(
        message["role"] == "user"
        for message in st.session_state.messages[question_index + 1 :]
    )


def current_stage():
    if not st.session_state.messages:
        return "meaning_question"

    if all_section_followups_complete():
        if final_open_question_index() is None:
            return "final_open_question"
        if not final_open_question_has_been_answered():
            return "final_open_answer"
        return "evaluation" if summary_has_been_asked() else "summary"

    for index, section in enumerate(SECTIONS):
        if section_start_index(section) is None:
            return f"{section['id']}_question"

        if followup_answer_count(section) < followups_required(section):
            return f"{section['id']}_followup"

        if index + 1 < len(SECTIONS) and section_start_index(SECTIONS[index + 1]) is None:
            return f"{SECTIONS[index + 1]['id']}_question"

    return "summary"


def active_section():
    stage = current_stage()
    for section in SECTIONS:
        if stage in (f"{section['id']}_question", f"{section['id']}_followup"):
            return section
    return None


def append_closed_answer(section, answer):
    if section["type"] == "slider":
        answer_text = f"{answer}{section.get('answer_suffix', '')}"
    elif section["type"] == "checkboxes_text":
        selected_options = answer.get("selected_options", [])
        free_text = answer.get("free_text", "").strip()
        answer_parts = []

        if selected_options:
            answer_parts.append("Selected options: " + "; ".join(selected_options))
        if free_text:
            answer_parts.append("Other response: " + free_text)

        answer_text = "\n".join(answer_parts)
    else:
        answer_text = str(answer).strip()

    st.session_state.messages.extend(
        [
            {"role": "assistant", "content": section_question_text(section)},
            {"role": "user", "content": answer_text},
        ]
    )
    save_backup(
        backups_directory=config.BACKUPS_DIRECTORY,
        admin_alias=config.ADMIN_ALIAS,
    )


def render_closed_question(section):
    transition_message = section.get("transition")

    if transition_message:
        with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
            st.markdown(transition_message)

    st.markdown(f"### {section['title']}")

    with st.form(f"{section['id']}_closed_form"):
        if section["type"] == "text":
            answer = st.text_area(section["question"])
        elif section["type"] == "slider":
            answer = st.slider(
                section["question"],
                min_value=0,
                max_value=10,
                value=5,
                step=1,
                key=f"{section['id']}_slider",
            )
        elif section["type"] == "radio":
            answer = st.radio(
                section["question"],
                section["options"],
                index=None,
                key=f"{section['id']}_radio",
            )
        elif section["type"] == "checkboxes_text":
            st.markdown(section["question"])
            selected_options = []

            for option_index, option in enumerate(section["options"]):
                if st.checkbox(
                    option,
                    key=f"{section['id']}_checkbox_{option_index}",
                ):
                    selected_options.append(option)

            free_text = st.text_area(
                section.get("text_label", "Other details:"),
                key=f"{section['id']}_free_text",
            )
            answer = {
                "selected_options": selected_options,
                "free_text": free_text,
            }
        else:
            raise ValueError(f"Unknown section type: {section['type']}")

        submitted = st.form_submit_button("Continue")

    if submitted:
        if section["type"] == "checkboxes_text":
            selected_options = answer["selected_options"]
            free_text = answer["free_text"].strip()
            exclusive_options = set(section.get("exclusive_options", []))

            if not selected_options and not free_text:
                st.warning("Please write or select an answer before continuing.")
                st.stop()

            if exclusive_options.intersection(selected_options) and len(selected_options) > 1:
                st.warning(
                    "Please select either 'None of these are absolute' or the other options, not both."
                )
                st.stop()
        elif answer is None or not str(answer).strip():
            st.warning("Please write or select an answer before continuing.")
            st.stop()

        append_closed_answer(section, answer)
        st.rerun()


def render_locked_control(section):
    st.markdown(f"### {section['title']}")
    answer = closed_answer(section)

    if section["type"] == "text":
        st.text_area(
            section["question"],
            value=answer or "",
            disabled=True,
            key=f"{section['id']}_locked_text",
        )
    elif section["type"] == "slider":
        try:
            value = int(str(answer).split(" out of 10")[0])
        except (TypeError, ValueError):
            value = 5

        st.slider(
            section["question"],
            min_value=0,
            max_value=10,
            value=value,
            step=1,
            disabled=True,
            key=f"{section['id']}_locked_slider",
        )
    elif section["type"] == "radio":
        index = section["options"].index(answer) if answer in section["options"] else 0
        st.radio(
            section["question"],
            section["options"],
            index=index,
            disabled=True,
            key=f"{section['id']}_locked_radio",
        )
    elif section["type"] == "checkboxes_text":
        answer_text = answer or ""
        st.markdown(section["question"])

        for option_index, option in enumerate(section["options"]):
            st.checkbox(
                option,
                value=option in answer_text,
                disabled=True,
                key=f"{section['id']}_locked_checkbox_{option_index}",
            )

        other_response = ""
        if "Other response: " in answer_text:
            other_response = answer_text.split("Other response: ", 1)[1]

        st.text_area(
            section.get("text_label", "Other details:"),
            value=other_response,
            disabled=True,
            key=f"{section['id']}_locked_free_text",
        )
    else:
        raise ValueError(f"Unknown section type: {section['type']}")


def render_section_conversation(section):
    for message in section_messages(section):
        avatar = (
            config.AVATAR_INTERVIEWER
            if message["role"] == "assistant"
            else config.AVATAR_RESPONDENT
        )
        with st.chat_message(message["role"], avatar=avatar):
            st.markdown(message["content"])


def normalize_text(text):
    return " ".join(str(text).lower().split())


def asks_closed_survey_item(message):
    normalized = normalize_text(message)
    closed_item_markers = [
        "what does democracy mean to you",
        "how important is it for you to live in a country that is governed democratically",
        "very satisfied, fairly satisfied, not very satisfied",
        "not at all satisfied with the way democracy works",
        "which areas most shaped your answer about how democracy works",
        "has your satisfaction with the way democracy works in your country changed over time",
        "what is more important to you: that a government be democratic even if it is not effective",
        "that the government be effective even if it is not democratic",
        "which of the following statements do you agree with most",
        "democracy is preferable to any other form of government",
        "authoritarian government may be preferable",
        "are there any things democratic leaders should not do",
        "refuse to leave office after losing an election",
    ]
    return any(marker in normalized for marker in closed_item_markers)


def asks_summary_too_early(message):
    normalized = normalize_text(message)
    summary_markers = [
        "here is a summary",
        "summary of your views",
        "summary of our discussion",
        "based on our conversation",
        "how well does the summary",
        "to conclude, how well",
    ]
    return any(marker in normalized for marker in summary_markers)


def fallback_followup_for_section(section):
    answer = closed_answer(section)
    answered = followup_answer_count(section)

    if section["id"] == "meaning":
        if answered == 0:
            return (
                "That is a helpful starting point. When you use the word democracy, what kinds of things are you thinking of?"
            )
        return (
            "I see. Does democracy mean something mainly political to you, or does it also connect to everyday life?"
        )

    if section["id"] == "importance":
        if answered == 0:
            return (
                f"You chose {answer}. What makes democracy feel that important, or not important, to you?"
            )
        return (
            "That helps explain your rating. Is your answer more about how decisions are made, the results government produces, or both?"
        )

    if section["id"] == "satisfaction":
        if answered == 0:
            return (
                f"You selected '{answer}'. What aspects of how democracy works in "
                "your country were most on your mind?"
            )
        return (
            "That gives me a better sense of what you were weighing. Is that based mostly on recent experiences, or on a longer-term view of how democracy works?"
        )

    if section["id"] == "satisfaction_drivers":
        if answered == 0:
            return "Those are useful anchors. Which of those areas mattered most for your view, and why?"
        if answered == 1:
            return (
                "That makes sense. Is this based on something you personally experienced, something people around you "
                "experienced, or a broader impression?"
            )
        return (
            "I understand the concern a bit better now. What would a better response from government look like to you?"
        )

    if section["id"] == "satisfaction_time":
        if answered == 0:
            return "That helps place your view in time. What experiences or events made your view change, or helped it stay the same?"
        return (
            "I see. Was this shaped mostly by recent events, or by things you learned or experienced earlier in life?"
        )

    if section["id"] == "effectiveness":
        if answered == 0:
            return "That choice gets at an important tradeoff. What makes that feel like the better option to you?"
        if answered == 1:
            return (
                "That helps clarify your instinct. Thinking about the issues you raised earlier, what would feel like the right balance "
                "between getting problems solved and keeping decisions democratic?"
            )
        if "effective even if it is not democratic" in str(answer).lower():
            return (
                "I can see why effectiveness matters in that case. What would worry you, if anything, about a government solving problems without democratic checks?"
            )
        return "I can see that democratic process still matters to you. When does a slower democratic process still feel worth preserving?"

    if section["id"] == "regime_preference":
        answer_text = str(answer).lower()
        if "authoritarian" in answer_text:
            if answered == 0:
                return "That is helpful to understand. What kinds of circumstances would make an authoritarian government seem preferable?"
            if answered == 1:
                return "I see the situation you have in mind. What would you expect that kind of government to do better?"
            return (
                "That helps explain the appeal. Some people worry that authoritarian governments may act faster but may also restrict "
                "people's freedoms or rights. How do you think about that tradeoff?"
            )
        if "no difference" in answer_text:
            if answered == 0:
                return (
                    "That is useful to hear. What makes the type of political system feel like it does not make much difference "
                    "for people like you?"
                )
            if answered == 1:
                return "I understand that sense of distance from politics. What, if anything, could make the type of political system matter more to you?"
            return "That helps explain your view. Are there any political rights or protections that would still matter to you personally?"
        if answered == 0:
            return "That is a clear preference. What makes democracy preferable to you, even when it has problems?"
        if answered == 1:
            return "I hear that you still see real problems. Are there situations where democracy's problems make that commitment harder for you?"
        return "That helps me understand your commitment to democracy. What would make you most concerned that democracy was being weakened?"

    if section["id"] == "red_lines":
        if answered == 0:
            return "Those choices help show where your boundaries are. Which of these feels most important to you, and why?"
        if answered == 1:
            return (
                "That boundary is useful to think through. Looking back at your earlier answers, how do you think about the boundary between "
                "wanting better results and preserving democratic rules?"
            )
        return (
            "I see the line you are drawing. What would go too far for you, even if a democratic leader promised better results?"
        )

    return (
        "That is helpful. Thinking about the question here though, what part of "
        "that feels most important to you?"
    )


def maybe_handle_closing_code(message_interviewer):
    for code, closing_message in config.CLOSING_MESSAGES.items():
        if code in message_interviewer:
            st.session_state.interview_active = False
            st.session_state.messages.append(
                {"role": "assistant", "content": message_interviewer}
            )
            st.session_state.messages.append(
                {"role": "assistant", "content": closing_message}
            )
            save_transcript_and_metadata(
                transcripts_directory=config.TRANSCRIPTS_DIRECTORY,
                metadata_directory=config.METADATA_DIRECTORY,
                api_kwargs=api_kwargs,
                admin_alias=config.ADMIN_ALIAS,
            )
            save_backup(
                backups_directory=config.BACKUPS_DIRECTORY,
                admin_alias=config.ADMIN_ALIAS,
            )
            st.rerun()


def generate_ai_message():
    with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
        message_placeholder = st.empty()
        message_placeholder.markdown("Let me think about that for a moment...")
        time.sleep(CONVERSATIONAL_PAUSE_SECONDS)
        message_placeholder.empty()
        message_interviewer = stream_response(
            client=client,
            client_kwargs=api_kwargs,
            message_placeholder=message_placeholder,
        )

    maybe_handle_closing_code(message_interviewer)

    section = active_section()
    if section is not None and (
        asks_closed_survey_item(message_interviewer)
        or asks_summary_too_early(message_interviewer)
    ):
        message_interviewer = fallback_followup_for_section(section)
        message_placeholder.markdown(message_interviewer)

    st.session_state.messages.append(
        {"role": "assistant", "content": message_interviewer}
    )
    save_backup(
        backups_directory=config.BACKUPS_DIRECTORY,
        admin_alias=config.ADMIN_ALIAS,
    )
    st.rerun()


def render_followup_input(section):
    answered = followup_answer_count(section)
    remaining = followups_required(section) - answered
    label = "Your answer"
    help_text = (
        f"This section has {remaining} answer"
        f"{'' if remaining == 1 else 's'} remaining before the next part."
    )

    with st.form(f"{section['id']}_answer_form_{len(st.session_state.messages)}"):
        response = st.text_area(label, help=help_text)
        submitted = st.form_submit_button("Submit answer")

    if submitted:
        if not response.strip():
            st.warning("Please write an answer before continuing.")
            st.stop()

        st.session_state.messages.append(
            {"role": "user", "content": response.strip()}
        )
        st.session_state.num_text_and_voice_answers[0] += 1
        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY,
            admin_alias=config.ADMIN_ALIAS,
        )
        st.rerun()


def render_summary_rating_input():
    with st.form("summary_rating_form"):
        rating = st.radio(
            "How well does the summary describe your views?",
            ["1", "2", "3", "4"],
            index=None,
        )
        submitted = st.form_submit_button("Finish interview")

    if submitted:
        if rating is None:
            st.warning("Please select a rating before finishing.")
            st.stop()

        st.session_state.messages.append({"role": "user", "content": rating})
        closing_message = config.CLOSING_MESSAGES["x7y8"]
        st.session_state.messages.append({"role": "assistant", "content": "x7y8"})
        st.session_state.messages.append(
            {"role": "assistant", "content": closing_message}
        )
        st.session_state.interview_active = False

        save_transcript_and_metadata(
            transcripts_directory=config.TRANSCRIPTS_DIRECTORY,
            metadata_directory=config.METADATA_DIRECTORY,
            api_kwargs=api_kwargs,
            admin_alias=config.ADMIN_ALIAS,
        )
        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY,
            admin_alias=config.ADMIN_ALIAS,
        )
        st.rerun()


def render_summary_page_header(is_review=False):
    eyebrow = "Final Review" if is_review else "Preparing Summary"
    title = "Review Your Interview Summary" if is_review else "Summary"
    subtitle = (
        "Please read the summary below, then rate how well it describes your views."
        if is_review
        else (
            "Thanks. Your interview responses are complete. The interviewer is now "
            "preparing a short summary for you to review."
        )
    )

    st.markdown(
        f"""
        <div class="summary-page-header">
            <div class="summary-page-eyebrow">{eyebrow}</div>
            <div class="summary-page-title">{title}</div>
            <div class="summary-page-subtitle">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_bottom_spacer():
    st.markdown('<div class="section-end-spacer"></div>', unsafe_allow_html=True)


def render_final_open_question():
    st.markdown("### Final Reflections")
    with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
        st.markdown(FINAL_OPEN_MESSAGE)

    st.session_state.messages.append(
        {"role": "assistant", "content": FINAL_OPEN_MESSAGE}
    )
    save_backup(
        backups_directory=config.BACKUPS_DIRECTORY,
        admin_alias=config.ADMIN_ALIAS,
    )
    st.rerun()


def render_final_open_answer_input():
    st.markdown("### Final Reflections")
    with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
        st.markdown(FINAL_OPEN_MESSAGE)

    with st.form("final_open_answer_form"):
        response = st.text_area("Your answer")
        submitted = st.form_submit_button("Continue")

    if submitted:
        if not response.strip():
            st.warning("Please write an answer before continuing.")
            st.stop()

        st.session_state.messages.append(
            {"role": "user", "content": response.strip()}
        )
        st.session_state.num_text_and_voice_answers[0] += 1
        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY,
            admin_alias=config.ADMIN_ALIAS,
        )
        st.rerun()


#
# 3. Render interview
#

col1, col2 = st.columns([0.75, 0.25])
with col2:
    if st.session_state.interview_active and st.button("Quit interview"):
        st.session_state.interview_active = False
        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY,
            admin_alias=config.ADMIN_ALIAS,
        )
        st.rerun()


if not st.session_state.interview_active:
    st.markdown(config.CLOSING_MESSAGES["x7y8"])
    st.stop()


stage = current_stage()
section = active_section()

if not st.session_state.messages:
    with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
        st.markdown(INTRO_MESSAGE)


if stage == "final_open_question":
    render_final_open_question()
    render_bottom_spacer()
    st.stop()


if stage == "final_open_answer":
    render_final_open_answer_input()
    render_bottom_spacer()
    st.stop()


if stage.endswith("_question"):
    render_closed_question(section)
    render_bottom_spacer()
    st.stop()


if stage.endswith("_followup"):
    render_locked_control(section)
    render_section_conversation(section)

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        generate_ai_message()

    render_followup_input(section)
    render_bottom_spacer()
    st.stop()


if stage == "summary":
    render_summary_page_header(is_review=False)
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        generate_ai_message()
    render_bottom_spacer()
    st.stop()


if stage == "evaluation":
    render_summary_page_header(is_review=True)
    for message in st.session_state.messages:
        if (
            message["role"] == "assistant"
            and "how well does the summary of our discussion describe your views about democracy"
            in message["content"].lower()
        ):
            with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
                st.markdown(message["content"])
            break

    render_summary_rating_input()
    render_bottom_spacer()
    st.stop()
