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
        "max_followups": 2,
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
        "max_followups": 2,
        "transition": (
            "Thank you. I'd now like to ask about how important democracy is to you personally."
        ),
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
        "max_followups": 2,
        "transition": (
            "Thanks. Now I'd like to turn from the idea of democracy to how democracy is actually working."
        ),
    },
    {
        "id": "satisfaction_drivers",
        "title": "Part 4: What Shapes Satisfaction",
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
            "Thanks. I'd now like to get a bit more specific about what shaped that view."
        ),
    },
    {
        "id": "satisfaction_time",
        "title": "Part 5: Satisfaction Over Time",
        "question": (
            "Has your satisfaction with the way democracy works in your country changed over time, "
            "or has it been fairly stable? Please describe what has shaped this view."
        ),
        "type": "text",
        "max_followups": 2,
        "transition": (
            "That's helpful. I'd now like to ask whether your views have changed over time."
        ),
    },
    {
        "id": "effectiveness",
        "title": "Part 6: Democracy Versus Effectiveness",
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
            "Thanks for explaining that. We'll now turn to a possible trade-off between "
            "democratic government and effective government."
        ),
    },
    {
        "id": "regime_preference",
        "title": "Part 7: Regime Preference",
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
            "Thank you. I'd now like to ask more directly how you think about democracy compared "
            "with other forms of government."
        ),
    },
    {
        "id": "red_lines",
        "title": "Part 8: Democratic Red Lines",
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
            "Thanks. For the last main part of the interview, I'd like to ask about boundaries "
            "for democratic leaders."
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


def fallback_followup_for_section(section):
    answer = closed_answer(section)
    answered = followup_answer_count(section)

    if section["id"] == "meaning":
        if answered == 0:
            return (
                "When you use the word democracy, what kinds of things are you thinking of?"
            )
        return (
            "Does democracy mean something mainly political to you, or does it also connect to everyday life?"
        )

    if section["id"] == "importance":
        if answered == 0:
            return (
                f"You chose {answer}. What makes democracy feel that important, or not important, to you?"
            )
        return (
            "Is your answer more about how decisions are made, the results government produces, or both?"
        )

    if section["id"] == "satisfaction":
        if answered == 0:
            return (
                f"You selected '{answer}'. What aspects of how democracy works in "
                "your country were most on your mind?"
            )
        return (
            "Is that based mostly on recent experiences, or on a longer-term view of how democracy works?"
        )

    if section["id"] == "satisfaction_drivers":
        if answered == 0:
            return "Which of those areas mattered most for your view, and why?"
        if answered == 1:
            return (
                "Is this based on something you personally experienced, something people around you "
                "experienced, or a broader impression?"
            )
        return (
            "What would a better response from government look like to you?"
        )

    if section["id"] == "satisfaction_time":
        if answered == 0:
            return "What experiences or events made your view change, or helped it stay the same?"
        return (
            "Was this shaped mostly by recent events, or by things you learned or experienced earlier in life?"
        )

    if section["id"] == "effectiveness":
        if answered == 0:
            return "What makes that feel like the better option to you?"
        if answered == 1:
            return (
                "Thinking about the issues you raised earlier, what would feel like the right balance "
                "between getting problems solved and keeping decisions democratic?"
            )
        if "effective even if it is not democratic" in str(answer).lower():
            return (
                "What would worry you, if anything, about a government solving problems without democratic checks?"
            )
        return "When does a slower democratic process still feel worth preserving to you?"

    if section["id"] == "regime_preference":
        answer_text = str(answer).lower()
        if "authoritarian" in answer_text:
            if answered == 0:
                return "What kinds of circumstances would make an authoritarian government seem preferable?"
            if answered == 1:
                return "What would you expect that kind of government to do better?"
            return (
                "Some people worry that authoritarian governments may act faster but may also restrict "
                "people's freedoms or rights. How do you think about that tradeoff?"
            )
        if "no difference" in answer_text:
            if answered == 0:
                return (
                    "What makes the type of political system feel like it does not make much difference "
                    "for people like you?"
                )
            if answered == 1:
                return "What, if anything, could make the type of political system matter more to you?"
            return "Are there any political rights or protections that would still matter to you personally?"
        if answered == 0:
            return "What makes democracy preferable to you, even when it has problems?"
        if answered == 1:
            return "Are there situations where democracy's problems make that commitment harder for you?"
        return "What would make you most concerned that democracy was being weakened?"

    if section["id"] == "red_lines":
        if answered == 0:
            return "Which of these feels most important to you, and why?"
        if answered == 1:
            return (
                "Looking back at your earlier answers, how do you think about the boundary between "
                "wanting better results and preserving democratic rules?"
            )
        return (
            "What would go too far for you, even if a democratic leader promised better results?"
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
