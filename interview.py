# Main application for running AI-led interviews
#
# AI interviewer: text
# Respondent: text and/or voice input (as set in config.py)


import os
import random
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

# Set page title and icon
st.set_page_config(page_title="Interview", page_icon=config.AVATAR_INTERVIEWER)

# Check if login screen is enabled
if config.LOGINS:
    # Check password (displays login screen)
    pwd_correct, username = check_password()
    if not pwd_correct:
        st.stop()
    else:
        st.session_state.username = username
# Otherwise ask the respondent to enter a username without password
else:
    # Until username confirmed, show the input and stop
    if "username" not in st.session_state:
        username_input = st.text_input(
            "Please enter a username to start the interview:",
            key="username_input",
        )
        if not username_input:
            # User hasn't typed anything yet, just render the page
            st.stop()
        if not username_input.strip().isalnum():
            st.warning("Username must be alphanumeric.")
            st.stop()

        # Save non-empty username and rerun
        st.session_state.username = username_input.strip()
        st.rerun()

# Directories
if not os.path.exists(config.TRANSCRIPTS_DIRECTORY):
    os.makedirs(config.TRANSCRIPTS_DIRECTORY)
if not os.path.exists(config.METADATA_DIRECTORY):
    os.makedirs(config.METADATA_DIRECTORY)
if not os.path.exists(config.BACKUPS_DIRECTORY):
    os.makedirs(config.BACKUPS_DIRECTORY)

# Check if interview has been completed and, if so, only display closing message
interview_previously_completed = is_transcript_saved(config.TRANSCRIPTS_DIRECTORY)
if "messages" not in st.session_state and interview_previously_completed:
    st.session_state.messages = []
    st.session_state.interview_active = False

    code_found = False
    try:
        messages, _, _, _ = load_backup(backups_directory=config.BACKUPS_DIRECTORY)
    except Exception:
        messages = []

    # Display closing message
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

# If interview has not yet been completed, initialise session states
if "interview_active" not in st.session_state:
    st.session_state.interview_active = True

# Start time for this login
if "start_time_current_login" not in st.session_state:
    st.session_state.start_time_current_login = time.time()

# Initialise messages, times using dashboard, and the number of voice and text answers
if "messages" not in st.session_state:
    # Returns [], time.time(), [], [0, 0] if no data in the backup directory is found
    (
        st.session_state.messages,
        st.session_state.start_time,
        st.session_state.times_previous_attempts,
        st.session_state.num_text_and_voice_answers,
    ) = load_backup(backups_directory=config.BACKUPS_DIRECTORY)

# Create Boolean to track if transcription of voice input is done
if "transcription_done" not in st.session_state:
    st.session_state.transcription_done = False

# Key for voice input element
if "voice_input_key" not in st.session_state:
    st.session_state.voice_input_key = random.uniform(0, 1)

# Button to cancel interview
col1, col2 = st.columns([0.75, 0.25])
with col2:
    if st.session_state.interview_active and st.button(
        "Quit interview", help="Logging back in will restore the chat."
    ):
        st.session_state.interview_active = False
        quit_message = "You have quit the interview for now."
        # Run before quit message, to not store that but only display it to the user
        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY, admin_alias=config.ADMIN_ALIAS
        )
        st.session_state.messages.append({"role": "assistant", "content": quit_message})
        st.rerun()

# Initialise API kwargs
if isinstance(config.ADDITIONAL_API_KWARGS, dict):
    api_kwargs = deepcopy(config.ADDITIONAL_API_KWARGS)
else:
    raise ValueError(
        "ADDITIONAL_API_KWARGS must be specified as a dictionary in config.py, either empty or containing valid additional API parameters."
    )

# The model parameter is shared across all APIs
api_kwargs["model"] = config.MODEL

# API clients
if config.API == "openai":

    client = OpenAI(api_key=st.secrets["KEY_OPENAI"])
    api_kwargs["stream"] = True

elif config.API == "anthropic":
    import anthropic

    client = anthropic.Anthropic(api_key=st.secrets["KEY_ANTHROPIC"])
    api_kwargs["system"] = config.SYSTEM_PROMPT
    # Set max tokens default if no value set in config.py
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

# Check if voice input is enabled
if config.INPUT_MODE not in ["text", "voice", "text_and_voice"]:
    raise ValueError(
        "Please set INPUT_MODE to 'text', 'voice', or 'text_and_voice' in config.py."
    )
if "voice" in config.INPUT_MODE:
    client_audio = OpenAI(
        api_key=st.secrets["KEY_OPENAI"],
    )


#
# 2. Display first message or previous conversation
#

# Define containers for the survey controls and chat history. The survey container
# is intentionally created first so the active slider/radio block stays above the
# follow-up prompt during each section.
survey_container = st.container()
transcript_container = st.container()

INTRO_MESSAGE = (
    "Hello! I'm glad to have the opportunity to speak with you today about democracy and political life. "
    "Thank you for taking part in this interview. I'm interested in your views in your own words."
)

IMPORTANCE_QUESTION = (
    "How important is it for you to live in a country that is governed democratically? "
    "On this scale where 0 means it is not at all important and 10 means absolutely important, "
    "what position would you choose?"
)

SATISFACTION_QUESTION = (
    "On the whole, are you very satisfied, fairly satisfied, not very satisfied, "
    "or not at all satisfied with the way democracy works in your country?"
)
SATISFACTION_OPTIONS = [
    "Very satisfied",
    "Fairly satisfied",
    "Not very satisfied",
    "Not at all satisfied",
]

PREFERENCE_QUESTION = (
    "Please tell me whether you strongly agree, agree, neither agree nor disagree, disagree, "
    "or strongly disagree with the following statement: Democracy may have problems, "
    "but it is better than any other form of government."
)
PREFERENCE_OPTIONS = [
    "Strongly agree",
    "Agree",
    "Neither agree nor disagree",
    "Disagree",
    "Strongly disagree",
]

# These are the app-managed closed-ended survey question/answer pairs. They are
# kept in messages for the transcript and model context, but hidden from the chat
# display so the respondent sees the active control instead of a chat echo.
APP_MANAGED_MESSAGE_INDEXES = {0, 1, 4, 5, 8, 9}


def render_intro_message():
    with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
        st.markdown(INTRO_MESSAGE)


def render_importance_slider(value=5, disabled=False, key="importance_slider"):
    return st.slider(
        IMPORTANCE_QUESTION,
        min_value=0,
        max_value=10,
        value=value,
        step=1,
        disabled=disabled,
        key=key,
    )


def render_locked_current_survey_control():
    stage = st.session_state.get("democracy_stage")

    if stage == "importance_followup" and "democracy_importance" in st.session_state:
        with survey_container:
            render_intro_message()
            st.markdown("### Importance Of Democracy")
            render_importance_slider(
                value=st.session_state.democracy_importance,
                disabled=True,
                key="importance_locked",
            )

    elif stage == "satisfaction_followup" and "democracy_satisfaction" in st.session_state:
        with survey_container:
            st.markdown("### Satisfaction With Democracy")
            st.radio(
                SATISFACTION_QUESTION,
                SATISFACTION_OPTIONS,
                index=SATISFACTION_OPTIONS.index(st.session_state.democracy_satisfaction),
                disabled=True,
                key="satisfaction_locked",
            )

    elif stage == "preference_followup" and "democracy_preference" in st.session_state:
        with survey_container:
            st.markdown("### Democracy Compared To Other Forms Of Government")
            st.radio(
                "Democracy may have problems, but it is better than any other form of government.",
                PREFERENCE_OPTIONS,
                index=PREFERENCE_OPTIONS.index(st.session_state.democracy_preference),
                disabled=True,
                key="preference_locked",
            )


def infer_democracy_stage(messages):
    message_count = len(messages)

    if message_count == 0:
        return "importance_question"
    if message_count <= 3:
        return "importance_followup"
    if message_count == 4:
        return "satisfaction_question"
    if message_count <= 7:
        return "satisfaction_followup"
    if message_count == 8:
        return "preference_question"
    if message_count <= 11:
        return "preference_followup"
    return "summary"


def restore_democracy_answers_from_messages():
    messages = st.session_state.messages

    if "democracy_importance" not in st.session_state and len(messages) > 1:
        try:
            st.session_state.democracy_importance = int(
                messages[1]["content"].split(" out of 10")[0]
            )
        except (KeyError, TypeError, ValueError):
            pass

    if "democracy_satisfaction" not in st.session_state and len(messages) > 5:
        satisfaction = messages[5].get("content")
        if satisfaction in SATISFACTION_OPTIONS:
            st.session_state.democracy_satisfaction = satisfaction

    if "democracy_preference" not in st.session_state and len(messages) > 9:
        preference = messages[9].get("content")
        if preference in PREFERENCE_OPTIONS:
            st.session_state.democracy_preference = preference


restore_democracy_answers_from_messages()

# Track which part of the staged democracy interview is currently active.
if "democracy_stage" not in st.session_state:
    st.session_state.democracy_stage = infer_democracy_stage(st.session_state.messages)

# First closed-ended item: importance of democracy.
if (
    not st.session_state.messages
    and st.session_state.interview_active
    and st.session_state.democracy_stage == "importance_question"
):
    with survey_container:
        render_intro_message()
        st.markdown("### Importance Of Democracy")

        with st.form("importance_form"):
            democracy_importance = render_importance_slider(
                value=5,
                key="importance_form_slider",
            )

            submitted = st.form_submit_button("Continue")

    if submitted:
        st.session_state.democracy_importance = democracy_importance
        st.session_state.democracy_stage = "importance_followup"

        opening_message = "What were you thinking about when you chose that answer?"

        st.session_state.messages = [
            {"role": "assistant", "content": IMPORTANCE_QUESTION},
            {"role": "user", "content": f"{democracy_importance} out of 10"},
            {"role": "assistant", "content": opening_message},
        ]

        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY,
            admin_alias=config.ADMIN_ALIAS,
        )

        st.rerun()

    st.stop()

render_locked_current_survey_control()

# In case the interview history is still empty, pass system prompt to model and
# generate and display the first message
if not st.session_state.messages and st.session_state.interview_active:
    with transcript_container:
        with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
            message_placeholder = st.empty()
            message_interviewer = stream_response(
                client, api_kwargs, message_placeholder
            )

    st.session_state.messages.append(
        {"role": "assistant", "content": message_interviewer}
    )
    save_backup(
        backups_directory=config.BACKUPS_DIRECTORY, admin_alias=config.ADMIN_ALIAS
    )
    st.rerun()

# Otherwise, display the previous conversation
else:
    with transcript_container:
        # Don't display system messages
        for message_index, message in enumerate(st.session_state.messages):
            if message_index in APP_MANAGED_MESSAGE_INDEXES:
                continue

            if message["role"] == "assistant":
                avatar = config.AVATAR_INTERVIEWER
            elif message["role"] == "user":
                avatar = config.AVATAR_RESPONDENT
            else:
                continue

            # Only display messages without codes
            if not any(code in message["content"] for code in config.CLOSING_MESSAGES):
                with st.chat_message(message["role"], avatar=avatar):
                    st.markdown(message["content"])


# Second closed-ended item: satisfaction with democracy.
if (
    st.session_state.interview_active
    and st.session_state.get("democracy_stage") == "satisfaction_question"
):
    with survey_container:
        st.markdown("### Satisfaction With Democracy")

        with st.form("satisfaction_form"):
            democracy_satisfaction = st.radio(
                SATISFACTION_QUESTION,
                SATISFACTION_OPTIONS,
                index=None,
            )

            submitted = st.form_submit_button("Continue")

    if submitted:
        if democracy_satisfaction is None:
            st.warning("Please select an answer before continuing.")
            st.stop()

        st.session_state.democracy_satisfaction = democracy_satisfaction
        st.session_state.democracy_stage = "satisfaction_followup"

        st.session_state.messages.extend(
            [
                {"role": "assistant", "content": SATISFACTION_QUESTION},
                {"role": "user", "content": democracy_satisfaction},
                {
                    "role": "assistant",
                    "content": "What were you thinking of when you responded that way?",
                },
            ]
        )

        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY,
            admin_alias=config.ADMIN_ALIAS,
        )

        st.rerun()

    st.stop()


# Third closed-ended item: democracy compared to other forms of government.
if (
    st.session_state.interview_active
    and st.session_state.get("democracy_stage") == "preference_question"
):
    with survey_container:
        st.markdown("### Democracy Compared To Other Forms Of Government")

        with st.form("preference_form"):
            democracy_preference = st.radio(
                "Democracy may have problems, but it is better than any other form of government.",
                PREFERENCE_OPTIONS,
                index=None,
            )

            submitted = st.form_submit_button("Continue")

    if submitted:
        if democracy_preference is None:
            st.warning("Please select an answer before continuing.")
            st.stop()

        st.session_state.democracy_preference = democracy_preference
        st.session_state.democracy_stage = "preference_followup"

        st.session_state.messages.extend(
            [
                {"role": "assistant", "content": PREFERENCE_QUESTION},
                {"role": "user", "content": democracy_preference},
                {
                    "role": "assistant",
                    "content": "What were you thinking about when you chose that answer?",
                },
            ]
        )

        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY,
            admin_alias=config.ADMIN_ALIAS,
        )

        st.rerun()

    st.stop()


#
# 3. Main chat if interview is active
#

if st.session_state.interview_active:
    # Container for the chat and voice input elements used by the respondent
    response_container = st.container()

    with response_container:
        # Divider between chat and inputs
        st.divider()

        # Written message input for respondent
        text_input_element = st.empty()
        if "text" in config.INPUT_MODE:
            text_response = text_input_element.chat_input(
                config.TEXT_INPUT_INSTRUCTIONS
            )
        else:
            text_response = None

        # Alternative voice input
        voice_input_element = st.empty()
        if "voice" in config.INPUT_MODE:
            voice_response = voice_input_element.audio_input(
                config.VOICE_INPUT_INSTRUCTIONS,
                key=st.session_state.voice_input_key,
            )
        else:
            voice_response = None

    # If respondent uses written input
    if text_response:
        message_respondent = text_response

        # Increase statistic for number of text answers given by respondent
        st.session_state.num_text_and_voice_answers[0] += 1

        with transcript_container:
            with st.chat_message("user", avatar=config.AVATAR_RESPONDENT):
                st.markdown(message_respondent)

    # If respondent uses voice input
    elif voice_response:
        # Do not display chat and voice inputs anymore for now
        text_input_element.empty()
        voice_input_element.empty()

        # Until message accepted
        message_respondent = None

        with transcript_container:
            # Show respondent transcription
            with st.chat_message("user", avatar=config.AVATAR_RESPONDENT):
                response_transcription_placeholder = st.empty()
                if not st.session_state.transcription_done:
                    response_transcription_placeholder.markdown(
                        "_Processing voice input ..._"
                    )

                    st.session_state.response_transcription = (
                        client_audio.audio.transcriptions.create(
                            model=config.MODEL_TRANSCRIPTION,
                            file=voice_response,
                        ).text
                    )

                    response_transcription_placeholder.markdown(
                        st.session_state.response_transcription
                    )
                    st.session_state.transcription_done = True
                else:
                    response_transcription_placeholder.markdown(
                        st.session_state.response_transcription
                    )

            # Ask them to accept or reject
            col_accept, col_reject = st.columns([1, 1])

            accept_button_placeholder = col_accept.empty()
            reject_button_placeholder = col_reject.empty()

            # Now create the buttons inside those placeholders
            accept_clicked = accept_button_placeholder.button(
                "Proceed with this transcription."
            )
            reject_clicked = reject_button_placeholder.button("Enter a new answer.")

            if reject_clicked or accept_clicked:
                st.session_state.transcription_done = False
                accept_button_placeholder.empty()
                reject_button_placeholder.empty()

            # If user rejects, reset voice input and rerun
            if reject_clicked:
                st.session_state.voice_input_key = random.uniform(0, 1)
                st.rerun()

            # If user accepts, then proceed with the chat API call
            if accept_clicked:
                message_respondent = st.session_state.response_transcription

                # Increase statistic for number of voice answers given by respondent
                st.session_state.num_text_and_voice_answers[1] += 1

                with response_container:
                    st.session_state.voice_input_key = random.uniform(0, 1)
                    chat_key = random.uniform(0, 1)

                    # Written message input for respondent
                    if "text" in config.INPUT_MODE:
                        text_response = text_input_element.chat_input(
                            config.TEXT_INPUT_INSTRUCTIONS,
                            key=chat_key,
                        )
                    else:
                        text_response = None

                    # Alternative voice input
                    if "voice" in config.INPUT_MODE:
                        voice_response = voice_input_element.audio_input(
                            config.VOICE_INPUT_INSTRUCTIONS,
                            key=st.session_state.voice_input_key,
                        )
                    else:
                        voice_response = None

    # If neither voice nor text input given so far, set message to None
    else:
        message_respondent = None

    # Once the user response has been given
    if message_respondent:
        # Append message
        st.session_state.messages.append(
            {"role": "user", "content": message_respondent}
        )

        # Move to the next closed-ended democracy item after each follow-up answer.
        if st.session_state.get("democracy_stage") == "importance_followup":
            st.session_state.democracy_stage = "satisfaction_question"
            save_backup(
                backups_directory=config.BACKUPS_DIRECTORY,
                admin_alias=config.ADMIN_ALIAS,
            )
            st.rerun()

        if st.session_state.get("democracy_stage") == "satisfaction_followup":
            st.session_state.democracy_stage = "preference_question"
            save_backup(
                backups_directory=config.BACKUPS_DIRECTORY,
                admin_alias=config.ADMIN_ALIAS,
            )
            st.rerun()

        if st.session_state.get("democracy_stage") == "preference_followup":
            st.session_state.democracy_stage = "summary"

        with transcript_container:
            # Generate and display interviewer response to message
            with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
                # Create placeholder for message in chat interface
                message_placeholder = st.empty()

                # Stream response from chat API
                message_interviewer = stream_response(
                    client=client,
                    client_kwargs=api_kwargs,
                    message_placeholder=message_placeholder,
                )

                # Display closing message if code is in the message and end interview
                for code in config.CLOSING_MESSAGES.keys():
                    if code in message_interviewer:
                        # Set flag
                        st.session_state.interview_active = False

                        # Display closing message
                        closing_message = config.CLOSING_MESSAGES[code]
                        st.markdown(closing_message)

                        st.session_state.messages.append(
                            {
                                "role": "assistant",
                                "content": message_interviewer,  # original message
                            }
                        )
                        st.session_state.messages.append(
                            {
                                "role": "assistant",
                                "content": closing_message,  # displayed message
                            }
                        )

                        # Store final transcript and backup
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

                        # Clear chat and voice input elements upon completion of the
                        # interview (seems slightly faster than relying only on rerun)
                        text_input_element.empty()
                        voice_input_element.empty()
                        st.rerun()

                # Otherwise (i.e. if no code in message) arrive here

                # Append message
                st.session_state.messages.append(
                    {"role": "assistant", "content": message_interviewer}
                )

                # Attempt a backup save but continue interview if writing fails
                try:
                    save_backup(
                        backups_directory=config.BACKUPS_DIRECTORY,
                        admin_alias=config.ADMIN_ALIAS,
                    )
                except Exception:
                    pass

        # Refresh to show a new voice_input element
        st.session_state.voice_input_key = random.uniform(0, 1)
        st.rerun()
