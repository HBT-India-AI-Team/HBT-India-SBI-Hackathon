import { useEffect, useRef, useState } from 'react';
import { BotBubble, TypingBubble, UserBubble } from './ChatBubble';
import RequirementsSheet from './RequirementsSheet';
import ProgressStepper from './ProgressStepper';
import {
  getApplication,
  getSessionState,
  postMessage,
  uploadDocument,
} from '../api/client';

const MOBILE_TYPES = ['mobile_otp', 'guardian_mobile_otp'];
const QUICK_REPLY_TYPES = ['product_confirm'];
const GUARDIAN_TYPES = ['guardian_consent', 'guardian_mobile_otp'];

export function pickActiveRequirement(requirements, scope) {
  if (!requirements) return null;
  for (const r of requirements) {
    const isGuardian = GUARDIAN_TYPES.includes(r.type);
    if (scope === 'guardian' && !isGuardian) continue;
    if (scope !== 'guardian' && isGuardian) continue;
    const basicMatch = ['NOT_STARTED', 'AWAITING_INPUT', 'REJECTED'].includes(r.state);
    const otpAwaiting = MOBILE_TYPES.includes(r.type) && r.state === 'VERIFYING';
    if (basicMatch || otpAwaiting) return r;
  }
  return null;
}

export default function ChatWindow({
  sessionId,
  applicationId,
  scope = null,
  onApplicationUpdate,
  onNeedsGuardian,
  onReadyForReview,
  onSubmitted,
  emptyHint,
}) {
  const [messages, setMessages] = useState([]);
  const [application, setApplication] = useState(null);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [debugOutcome, setDebugOutcome] = useState('verify');
  const [docBusy, setDocBusy] = useState(false);
  const bottomRef = useRef(null);
  const fileInputRef = useRef(null);
  const initialized = useRef(false);

  const refreshApplication = async () => {
    const app = await getApplication(applicationId);
    setApplication(app);
    onApplicationUpdate && onApplicationUpdate(app);
    return app;
  };

  useEffect(() => {
    if (!sessionId || !applicationId || initialized.current) return;
    initialized.current = true;
    (async () => {
      try {
        const [state, app] = await Promise.all([getSessionState(sessionId), getApplication(applicationId)]);
        setApplication(app);
        onApplicationUpdate && onApplicationUpdate(app);
        const hydrated = (state.messages || []).map((m) => ({
          direction: m.direction,
          text: m.content?.text || '',
          variant: 'default',
        }));
        setMessages(hydrated);
      } catch (e) {
        console.error('chat hydrate failed', e);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, applicationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const activeReq = pickActiveRequirement(application?.requirements, scope);

  useEffect(() => {
    if (!application || scope === 'guardian') return;
    const reviewReq = (application.requirements || []).find((r) => r.type === 'review_submit');
    if (reviewReq && reviewReq.state === 'VERIFIED') {
      onSubmitted && onSubmitted(application);
      return;
    }
    if (activeReq?.type === 'review_submit') {
      onReadyForReview && onReadyForReview(application);
      return;
    }
    if (activeReq) return;
    const guardianPending = (application.requirements || []).find(
      (r) => r.type === 'guardian_consent' && ['NOT_STARTED', 'AWAITING_INPUT'].includes(r.state)
    );
    if (guardianPending && onNeedsGuardian) onNeedsGuardian(guardianPending);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application, activeReq, scope]);

  const send = async (text) => {
    const trimmed = (text ?? input).trim();
    if (!trimmed || loading) return;
    setMessages((prev) => [...prev, { direction: 'inbound', text: trimmed }]);
    setInput('');
    setLoading(true);
    try {
      const res = await postMessage(sessionId, trimmed);
      const rejected = (res.actions_applied || []).some((a) => a.result === 'rejected');
      setMessages((prev) => [
        ...prev,
        { direction: 'outbound', text: res.reply_text, variant: rejected ? 'error' : 'default' },
      ]);
      await refreshApplication();
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { direction: 'outbound', text: 'Something went wrong reaching the server. Please try again.', variant: 'error' },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleFilePick = () => fileInputRef.current?.click();

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !activeReq) return;
    setDocBusy(true);
    setMessages((prev) => [...prev, { direction: 'inbound', text: `📎 Uploaded: ${file.name}` }]);
    try {
      const form = new FormData();
      form.append('requirement_id', activeReq.id);
      form.append('file', file);
      if (debugOutcome !== 'random') form.append('debug_outcome', debugOutcome);
      await uploadDocument(applicationId, form);
      setMessages((prev) => [
        ...prev,
        { direction: 'outbound', text: `Thanks! "${activeReq.label}" is being reviewed — this usually takes a few seconds in this demo.` },
      ]);
      await pollDocument(activeReq.id);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { direction: 'outbound', text: 'Upload failed — please check the file and try again.', variant: 'error' },
      ]);
    } finally {
      setDocBusy(false);
    }
  };

  const pollDocument = async (requirementId) => {
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const app = await refreshApplication();
      const req = (app.requirements || []).find((r) => r.id === requirementId);
      if (!req) return;
      if (req.state === 'VERIFIED') {
        setMessages((prev) => [
          ...prev,
          { direction: 'outbound', text: `✅ "${req.label}" verified successfully.` },
        ]);
        return;
      }
      if (req.state === 'REJECTED' || req.state === 'ESCALATED') {
        setMessages((prev) => [
          ...prev,
          {
            direction: 'outbound',
            variant: 'error',
            text:
              req.state === 'ESCALATED'
                ? `We couldn't verify "${req.label}" after a couple of tries, so we've flagged it for our support team to review manually.`
                : `We couldn't read "${req.label}" clearly — the image may be blurry or the wrong document. Please try uploading again.`,
          },
        ]);
        return;
      }
    }
  };

  const showQuickReplies = activeReq && QUICK_REPLY_TYPES.includes(activeReq.type);
  const showDocUpload = activeReq && activeReq.type === 'document';
  const showOtpHint = activeReq && MOBILE_TYPES.includes(activeReq.type) && activeReq.state === 'VERIFYING';

  return (
    <div className="flex flex-col flex-1">
      {application?.progress && <ProgressStepper progress={application.progress} />}
      <button
        onClick={() => setSheetOpen(true)}
        className="self-end mb-3 text-[12px] font-semibold text-primary underline underline-offset-2"
      >
        View checklist
      </button>
      <div className="flex-1 flex flex-col gap-3 overflow-y-auto pb-3">
        {messages.length === 0 && emptyHint && <BotBubble>{emptyHint}</BotBubble>}
        {messages.map((m, i) =>
          m.direction === 'inbound' ? (
            <UserBubble key={i}>{m.text}</UserBubble>
          ) : (
            <BotBubble key={i} variant={m.variant}>
              {m.text}
            </BotBubble>
          )
        )}
        {loading && <TypingBubble />}
        <div ref={bottomRef} />
      </div>

      {showOtpHint && (
        <p className="text-[11.5px] text-on-surface-variant mb-2 bg-surface-container-low rounded-lg p-2">
          A 6-digit code was sent (mocked in this sandbox — check the backend server console/log for the code).
        </p>
      )}

      {showDocUpload && (
        <div className="mb-2 flex items-center gap-2">
          <select
            value={debugOutcome}
            onChange={(e) => setDebugOutcome(e.target.value)}
            className="text-[11px] bg-surface-container-low border border-outline-variant rounded-full px-2 py-1 text-on-surface-variant"
            title="Demo control: forces the async review outcome for this upload"
          >
            <option value="verify">Demo outcome: verify</option>
            <option value="reject">Demo outcome: reject</option>
            <option value="random">Demo outcome: default</option>
          </select>
          <button
            onClick={handleFilePick}
            disabled={docBusy}
            className="flex-1 h-11 rounded-full bg-primary text-on-primary font-heading font-bold text-[13px] disabled:opacity-50"
          >
            {docBusy ? 'Uploading…' : `📎 Upload: ${activeReq.label}`}
          </button>
          <input ref={fileInputRef} type="file" accept="image/*,.pdf" className="hidden" onChange={handleFile} />
        </div>
      )}

      {showQuickReplies && (
        <div className="flex gap-2 mb-2">
          <button
            onClick={() => send('yes')}
            className="flex-1 h-11 rounded-full bg-primary text-on-primary font-heading font-bold text-[13px]"
          >
            Yes
          </button>
          <button
            onClick={() => send('no')}
            className="flex-1 h-11 rounded-full border-2 border-primary text-primary font-heading font-bold text-[13px]"
          >
            No
          </button>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
        className="flex items-center gap-2 bg-surface-container-low rounded-full px-3 py-2 border border-outline-variant"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={activeReq ? `Type: ${activeReq.format_hint || activeReq.label}` : 'Type a message…'}
          className="flex-1 bg-transparent border-none outline-none text-[14px] text-on-surface placeholder:text-outline"
        />
        <button
          type="submit"
          disabled={loading}
          className="w-9 h-9 rounded-full bg-primary text-on-primary flex items-center justify-center shrink-0 disabled:opacity-50"
        >
          ➤
        </button>
      </form>

      <RequirementsSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        requirements={application?.requirements || []}
      />
    </div>
  );
}
