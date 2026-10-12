"""Import all models so Alembic can detect them."""

from app.models.team import Team, TeamMember
from app.models.meeting import Meeting
from app.models.transcription import Transcription
from app.models.memoir_summary import MemoirSummary
from app.models.weekly_digest import WeeklyDigest
from app.models.calendar import CalendarConnection, CalendarEvent
from app.models.transcription_job import TranscriptionJob
from app.models.slack import SlackConnection, SlackNotificationSettings
from app.models.email import EmailTemplate, EmailLog
from app.models.speaker import SpeakerProfile, MeetingParticipant, VoiceEmbeddingSample
from app.models.call_intelligence import (
    CallIntelligence,
    CallPainPoint,
    CallFeatureRequest,
    CallCompetitor,
    CallObjection,
    CallQuestion,
    IntelligenceDigest,
    ScheduledJob,
    ScheduledJobRun,
    ShareLink,
)
from app.models.notification import NotificationPreference, NotificationLog
from app.models.meeting_note import MeetingNote
from app.models.memoir_deal import MemoirDeal
from app.models.deal_match_log import DealMatchLog
from app.models.ai_prompt_log import AiPromptLog
from app.models.crm_push_audit import CrmPushAudit
from app.models.pmf_weekly_digest import PmfWeeklyDigest
from app.models.tab_audio_session import TabAudioUploadSession, TabAudioUploadChunk
