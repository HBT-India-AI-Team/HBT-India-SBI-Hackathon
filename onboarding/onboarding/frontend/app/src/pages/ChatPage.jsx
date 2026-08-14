import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import ChatWindow from '../components/ChatWindow';
import { useApp } from '../context/AppContext';

export default function ChatPage() {
  const navigate = useNavigate();
  const { applicationId, sessionId, patch } = useApp();

  if (!applicationId || !sessionId) {
    navigate('/');
    return null;
  }

  return (
    <PhoneScreen
      title="YONO Assistant"
      right={
        <button
          onClick={() => navigate('/support')}
          className="w-8 h-8 rounded-full bg-surface-container-low flex items-center justify-center text-sm"
          title="Get help"
        >
          ❓
        </button>
      }
    >
      <ChatWindow
        sessionId={sessionId}
        applicationId={applicationId}
        emptyHint="Hi! Let's get your account set up — send me your details whenever you're ready."
        onApplicationUpdate={(app) => patch({ application: app })}
        onNeedsGuardian={() => navigate('/guardian')}
        onReadyForReview={() => navigate('/review')}
        onSubmitted={() => navigate('/under-review')}
      />
    </PhoneScreen>
  );
}
