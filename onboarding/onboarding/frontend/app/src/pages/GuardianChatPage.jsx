import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import ChatWindow, { pickActiveRequirement } from '../components/ChatWindow';
import { useApp } from '../context/AppContext';

export default function GuardianChatPage() {
  const navigate = useNavigate();
  const { applicationId, guardianSessionId, patch } = useApp();

  if (!applicationId || !guardianSessionId) {
    navigate('/guardian');
    return null;
  }

  return (
    <PhoneScreen title="Guardian verification">
      <ChatWindow
        sessionId={guardianSessionId}
        applicationId={applicationId}
        scope="guardian"
        emptyHint="Hi! Please confirm your consent as the guardian on this account."
        onApplicationUpdate={(app) => {
          patch({ application: app });
          const stillActive = pickActiveRequirement(app.requirements, 'guardian');
          if (!stillActive) {
            // guardian part done -> rejoin the main applicant flow
            setTimeout(() => navigate('/chat'), 1200);
          }
        }}
      />
    </PhoneScreen>
  );
}
