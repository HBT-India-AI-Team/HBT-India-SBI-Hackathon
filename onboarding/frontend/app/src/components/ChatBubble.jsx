export function BotBubble({ children, variant = 'default' }) {
  const styles = {
    default: 'bg-surface-container-low border border-surface-highest text-on-surface',
    error: 'bg-error-container border border-error/30 text-on-error-container',
  };
  return (
    <div className="flex items-end gap-2 self-start max-w-[88%] chat-in">
      <div className="w-8 h-8 rounded-full bg-primary-container text-white flex items-center justify-center shrink-0 text-[15px]">
        🤖
      </div>
      <div className={`p-3.5 rounded-2xl rounded-bl-sm shadow-sm text-[14.5px] leading-snug ${styles[variant]}`}>
        {children}
      </div>
    </div>
  );
}

export function UserBubble({ children }) {
  return (
    <div className="self-end max-w-[85%] chat-in">
      <div className="p-3.5 rounded-2xl rounded-br-sm bg-primary text-on-primary text-[14.5px] leading-snug shadow-sm">
        {children}
      </div>
    </div>
  );
}

export function TypingBubble() {
  return (
    <div className="flex items-end gap-2 self-start max-w-[60%] chat-in">
      <div className="w-8 h-8 rounded-full bg-primary-container text-white flex items-center justify-center shrink-0 text-[15px]">
        🤖
      </div>
      <div className="px-4 py-3.5 rounded-2xl rounded-bl-sm bg-surface-container-low border border-surface-highest flex gap-1 items-center">
        <span className="w-1.5 h-1.5 rounded-full bg-outline typing-dot" style={{ animationDelay: '0s' }} />
        <span className="w-1.5 h-1.5 rounded-full bg-outline typing-dot" style={{ animationDelay: '0.15s' }} />
        <span className="w-1.5 h-1.5 rounded-full bg-outline typing-dot" style={{ animationDelay: '0.3s' }} />
      </div>
    </div>
  );
}
