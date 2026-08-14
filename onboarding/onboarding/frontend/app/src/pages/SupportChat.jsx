import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { BotBubble, TypingBubble, UserBubble } from '../components/ChatBubble';
import { useApp } from '../context/AppContext';
import { escalateSupport } from '../api/client';

export default function SupportChat() {
  const navigate = useNavigate();
  const { applicationId, sessionId } = useApp();
  const [ticket, setTicket] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const started = useRef(false);

  useEffect(() => {
    if (!applicationId || started.current) return;
    started.current = true;
    (async () => {
      try {
        const res = await escalateSupport(applicationId, {
          session_id: sessionId,
          reason: 'Customer requested human support from the app',
        });
        setTicket(res);
        setMessages([
          {
            from: 'bot',
            text: `You're connected. Ticket #${res.ticket_id.slice(0, 8)} has been raised with our support team — a human agent will pick this up shortly.`,
          },
        ]);
      } catch (e) {
        setMessages([{ from: 'bot', text: 'Could not reach support right now. Please try again shortly.' }]);
      }
    })();
  }, [applicationId, sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const send = () => {
    const text = input.trim();
    if (!text) return;
    setMessages((prev) => [...prev, { from: 'user', text }]);
    setInput('');
    setLoading(true);
    // NOTE: mocked agent reply -- there is no live human-agent websocket/API in
    // this backend build. The escalation ticket above is a real backend call;
    // this canned reply just represents "an agent has your message".
    setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        { from: 'bot', text: "Thanks for the details — noted on your ticket. Our team will follow up on your registered mobile number shortly." },
      ]);
      setLoading(false);
    }, 1200);
  };

  if (!applicationId) {
    navigate('/support');
    return null;
  }

  return (
    <PhoneScreen title="Support">
      <p className="text-[11px] text-on-surface-variant bg-surface-container-low rounded-lg p-2 mb-3">
        Mocked: agent replies below are simulated for this demo. The escalation ticket itself
        {ticket ? ` (#${ticket.ticket_id.slice(0, 8)})` : ''} is created for real via POST /support/escalate.
      </p>
      <div className="flex-1 flex flex-col gap-3 overflow-y-auto pb-3">
        {messages.map((m, i) =>
          m.from === 'user' ? <UserBubble key={i}>{m.text}</UserBubble> : <BotBubble key={i}>{m.text}</BotBubble>
        )}
        {loading && <TypingBubble />}
        <div ref={bottomRef} />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
        className="flex items-center gap-2 bg-surface-container-low rounded-full px-3 py-2 border border-outline-variant mt-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message…"
          className="flex-1 bg-transparent border-none outline-none text-[14px]"
        />
        <button type="submit" className="w-9 h-9 rounded-full bg-primary text-on-primary flex items-center justify-center">
          ➤
        </button>
      </form>
    </PhoneScreen>
  );
}
