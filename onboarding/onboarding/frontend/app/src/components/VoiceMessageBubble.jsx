function formatDuration(sec) {
  if (sec == null || Number.isNaN(sec)) return '--:--';
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Distinct chat-message type for a recorded/spoken voice message (Prompt 2):
// a compact audio player + duration, with the transcript/reply text shown as
// a caption underneath -- even once transcription is available, the message
// stays an audio bubble rather than collapsing into a plain text bubble.
export default function VoiceMessageBubble({
  direction, // 'inbound' | 'outbound'
  audioUrl,
  duration,
  text,
  status, // 'queued' | 'processing' | 'done' | 'error' | undefined
  statusText,
  variant = 'default',
  autoPlay = false,
}) {
  const isUser = direction === 'inbound';
  const bubbleStyles = isUser
    ? 'bg-primary text-on-primary'
    : variant === 'error'
      ? 'bg-error-container border border-error/30 text-on-error-container'
      : 'bg-surface-container-low border border-surface-highest text-on-surface';

  const content = (
    <div className={`p-3 rounded-2xl ${isUser ? 'rounded-br-sm' : 'rounded-bl-sm'} shadow-sm ${bubbleStyles}`}>
      <div className="flex items-center gap-2">
        <span className="text-[15px] shrink-0">🎤</span>
        {audioUrl ? (
          <audio
            controls
            autoPlay={autoPlay}
            src={audioUrl}
            className="h-8 max-w-[190px]"
            style={{ filter: isUser ? 'invert(1) hue-rotate(180deg)' : 'none' }}
          />
        ) : (
          <span className="text-[12.5px] opacity-70">…</span>
        )}
        {duration != null && (
          <span className="text-[11px] opacity-70 tabular-nums shrink-0">{formatDuration(duration)}</span>
        )}
      </div>

      {(status === 'queued' || status === 'processing') && (
        <p className="flex items-center gap-1.5 text-[11.5px] mt-1.5 opacity-80">
          <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
          {statusText || (status === 'queued' ? 'Queued…' : 'Processing…')}
        </p>
      )}

      {text && status !== 'processing' && status !== 'queued' && (
        <p className="text-[13px] mt-1.5 leading-snug opacity-90">{text}</p>
      )}

      {status === 'error' && (
        <p className="text-[11.5px] mt-1.5 font-semibold" style={{ color: isUser ? undefined : 'var(--color-error)' }}>
          {statusText || 'Something went wrong.'}
        </p>
      )}
    </div>
  );

  return isUser ? (
    <div className="self-end max-w-[85%] chat-in">{content}</div>
  ) : (
    <div className="flex items-end gap-2 self-start max-w-[88%] chat-in">
      <div className="w-8 h-8 rounded-full bg-primary-container text-white flex items-center justify-center shrink-0 text-[15px]">
        🤖
      </div>
      {content}
    </div>
  );
}
