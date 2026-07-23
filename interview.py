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

if config.LOGINS:
    pwd_correct, username = check_password()
    if not pwd_correct:
        st.stop()
    st.session_state.username = username
else:
    if "username" not in st.session_state:
        username_input = st.text_input(
            "Please enter a username to start the interview:",
            key="username_input",
        )
        if not username_input:
            st.stop()
        if not username_input.strip().isalnum():
            st.warning("Username must be alphanumeric.")
            st.stop()

        st.session_state.username = username_input.strip()
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

SECTIONS = [
    {
        "id": "meaning",
        "title": "Part 1: Meaning Of Democracy",
        "question": "What does democracy mean to you?",
        "type": "text",
    },
    {
        "id": "importance",
        "title": "Part 2: Importance Of Democracy",
        "question": (
            "How important is it for you to live in a country that is governed democratically? "
            "On this scale where 0 means it is not at all important and 10 means absolutely important, "
            "what position would you choose?"
        ),
        "type": "slider",
        "answer_suffix": " out of 10",
    },
    {
        "id": "satisfaction",
        "title": "Part 3: Satisfaction With Democracy",
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
    },
    {
        "id": "preference",
        "title": "Part 4: Democracy Compared To Other Forms Of Government",
        "question": (
            "Democracy may have problems, but it is better than any other form of government."
        ),
        "type": "radio",
        "options": [
            "Strongly agree",
            "Agree",
            "Neither agree nor disagree",
            "Disagree",
            "Strongly disagree",
        ],
        "transcript_question": (
            "Please tell me whether you strongly agree, agree, neither agree nor disagree, disagree, "
            "or strongly disagree with the following statement: Democracy may have problems, "
            "but it is better than any other form of government."
        ),
    },
]


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

    # Hide the app-managed closed-ended survey question and answer from the
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
        and followup_answer_count(section) >= MAX_FOLLOWUPS_PER_SECTION
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

        if followup_answer_count(section) < MAX_FOLLOWUPS_PER_SECTION:
            return f"{section['id']}_followup"

        if index + 1 < len(SECTIONS) and section_start_index(SECTIONS[index + 1]) is None:
            return f"{SECTIONS[index + 1]['id']}_question"

    return "summary"


def active_section():
    stage = current_stage()
    for section in SECTIONS:
        if stage.startswith(section["id"]):
            return section
    return None


def append_closed_answer(section, answer):
    answer_text = (
        f"{answer}{section.get('answer_suffix', '')}"
        if section["type"] == "slider"
        else str(answer).strip()
    )

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
    transition_message = {
        "importance": (
            "Thanks, that's helpful. We'll now move to the next part of the conversation, "
            "about how important it is to you to live in a country that is governed democratically."
        ),
        "satisfaction": (
            "Thanks for answering those questions. We'll now move to the next part "
            "of the conversation, about how satisfied you are with the way democracy "
            "works in your country."
        ),
        "preference": (
            "Thanks for sharing that. We'll now move to the final main part of the "
            "conversation, about how you think about democracy compared with other "
            "forms of government."
        ),
    }.get(section["id"])

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
        else:
            answer = st.radio(
                section["question"],
                section["options"],
                index=None,
                key=f"{section['id']}_radio",
            )

        submitted = st.form_submit_button("Continue")

    if submitted:
        if answer is None or not str(answer).strip():
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
    else:
        index = section["options"].index(answer) if answer in section["options"] else 0
        st.radio(
            section["question"],
            section["options"],
            index=index,
            disabled=True,
            key=f"{section['id']}_locked_radio",
        )


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
        "democracy may have problems",
        "better than any other form of government",
    ]
    return any(marker in normalized for marker in closed_item_markers)


def fallback_followup_for_section(section):
    answer = closed_answer(section)
    answered = followup_answer_count(section)

    if section["id"] == "meaning":
        if answered == 0:
            return (
                "When you think about democracy that way, what feels most important "
                "about it in ordinary political life?"
            )
        return (
            "Could you give a concrete example of what would make a country feel "
            "more or less democratic to you?"
        )

    if section["id"] == "importance":
        if answered == 0:
            return (
                f"You chose {answer}. What does being governed democratically mean "
                "to you in practice?"
            )
        return (
            "Could you give a concrete example of the kind of democratic feature "
            "or problem you had in mind?"
        )

    if section["id"] == "satisfaction":
        if answered == 0:
            return (
                f"You selected '{answer}'. What aspects of how democracy works in "
                "your country were most on your mind?"
            )
        return (
            "Could you say a bit more about a specific experience, institution, "
            "or event that shaped that view?"
        )

    if section["id"] == "preference":
        if answered == 0:
            return (
                f"You selected '{answer}'. What makes democracy seem better, or not "
                "better, than other forms of government to you?"
            )
        return (
            "What kinds of problems with democracy, or possible alternatives, were "
            "you comparing in your mind?"
        )

    return "Could you tell me a little more about what shaped that answer?"


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
        message_interviewer = stream_response(
            client=client,
            client_kwargs=api_kwargs,
            message_placeholder=message_placeholder,
        )

    maybe_handle_closing_code(message_interviewer)

    section = active_section()
    if section is not None and asks_closed_survey_item(message_interviewer):
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
    remaining = MAX_FOLLOWUPS_PER_SECTION - answered
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
    st.stop()


if stage == "final_open_answer":
    render_final_open_answer_input()
    st.stop()


if stage.endswith("_question"):
    render_closed_question(section)
    st.stop()


if stage.endswith("_followup"):
    render_locked_control(section)
    render_section_conversation(section)

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        generate_ai_message()

    render_followup_input(section)
    st.stop()


if stage == "summary":
    st.markdown("### Summary")
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        generate_ai_message()
    st.stop()


if stage == "evaluation":
    st.markdown("### Summary")
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
    st.stop()
