import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { GhostButton } from '../components/PrimaryButton';
import { getProfileId } from '../lib/finguruProfile';
import { listConversations, deleteConversation, clearAllHistory, historySupported } from '../lib/historyDb';
import NamePrompt from '../components/NamePrompt';
import { useFinGuruName } from '../lib/finguruIdentity';

function formatWhen(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

export default function FinGuruHistory() {
  const navigate = useNavigate();
  const { name, setName } = useFinGuruName();
  const [conversations, setConversations] = useState([]);
  const [loaded, setLoaded] = useState(false);

  const reload = async () => {
    if (!historySupported) {
      setLoaded(true);
      return;
    }
    const list = await listConversations(getProfileId()).catch(() => []);
    setConversations(list);
    setLoaded(true);
  };

  useEffect(() => {
    reload();
  }, []);

  const openConversation = (id) => navigate('/finguru/chat', { state: { conversationId: id } });

  const startNew = () => {
    const id =
      (typeof crypto !== 'undefined' && crypto.randomUUID && crypto.randomUUID()) ||
      `c-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    navigate('/finguru/chat', { state: { conversationId: id, isNew: true } });
  };

  const removeOne = async (e, id) => {
    e.stopPropagation();
    await deleteConversation(id).catch(() => {});
    reload();
  };

  const clearAll = async () => {
    if (!window.confirm('Delete all FinGuru conversation history on this device? This cannot be undone.')) return;
    await clearAllHistory(getProfileId()).catch(() => {});
    reload();
  };

  if (!name) return <NamePrompt onSubmit={setName} />;

  return (
    <PhoneScreen title="Conversation history">
      <div className="flex flex-col gap-3">
        <GhostButton onClick={startNew}>+ Start a new conversation</GhostButton>

        {!historySupported && (
          <p className="text-[12.5px] text-on-surface-variant bg-surface-container-low rounded-lg p-3">
            This browser doesn't support local history storage, so past conversations aren't saved here.
          </p>
        )}

        {historySupported && loaded && conversations.length === 0 && (
          <p className="text-[12.5px] text-on-surface-variant text-center mt-4">
            No saved conversations yet — they'll show up here once you chat with FinGuru.
          </p>
        )}

        {conversations.map((c) => (
          <button
            key={c.id}
            onClick={() => openConversation(c.id)}
            className="text-left bg-surface-container-low border border-surface-highest rounded-xl p-3 flex items-center justify-between gap-2 active:scale-[0.99] transition"
          >
            <div className="min-w-0">
              <p className="text-[13.5px] font-semibold text-on-surface truncate">{c.title || 'Voice conversation'}</p>
              <p className="text-[11px] text-on-surface-variant">
                {formatWhen(c.updatedAt)} · {(c.messages || []).length} message{(c.messages || []).length === 1 ? '' : 's'}
              </p>
            </div>
            <button
              type="button"
              onClick={(e) => removeOne(e, c.id)}
              aria-label="Delete conversation"
              title="Delete"
              className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-error"
            >
              🗑️
            </button>
          </button>
        ))}

        {historySupported && conversations.length > 0 && (
          <button onClick={clearAll} className="text-[12px] font-semibold text-error mt-2 self-center underline underline-offset-2">
            Clear all history
          </button>
        )}
      </div>
    </PhoneScreen>
  );
}
