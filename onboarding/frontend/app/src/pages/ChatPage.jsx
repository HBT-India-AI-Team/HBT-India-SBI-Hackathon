import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import ChatWindow from '../components/ChatWindow';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

// Reachable at /onboarding. An external caller can deep-link here with known
// details, e.g. /onboarding?name=Asha&mobile=9876543210 -- those are captured
// into AppContext (shown in the chat greeting, pre-filled at the mobile-number
// step in RequirementsChecklist) but landing here fresh NEVER skips the
// consent screens: with no active session yet, this still routes into the
// normal language -> product -> consent -> requirements funnel, just with the
// caller's details already known by the time the user gets there.
export default function ChatPage() {
  const navigate = useNavigate();
  const t = useT();
  const [searchParams] = useSearchParams();
  const { applicationId, sessionId, name, patch } = useApp();

  useEffect(() => {
    const paramName = searchParams.get('name');
    const paramMobile = searchParams.get('mobile');
    const paramProduct = searchParams.get('product');
    const updates = {};
    if (paramName) updates.name = paramName;
    if (paramMobile) updates.mobileNumber = paramMobile;
    if (paramProduct) updates.productId = paramProduct;
    if (Object.keys(updates).length) patch(updates);

    if (!applicationId || !sessionId) {
      navigate('/language', { replace: true });
    }
  }, [searchParams, applicationId, sessionId, navigate, patch]);

  if (!applicationId || !sessionId) return null;

  return (
    <PhoneScreen
      title={t('Onboarding Assistant')}
      right={
        <button
          onClick={() => navigate('/support')}
          className="w-8 h-8 rounded-full bg-surface-container-low flex items-center justify-center text-sm"
          title={t('Get help')}
        >
          ❓
        </button>
      }
    >
      <ChatWindow
        sessionId={sessionId}
        applicationId={applicationId}
        emptyHint={
          name
            ? t("Hi {name}! Let's get your account set up — send me your details whenever you're ready.", { name })
            : t("Hi! Let's get your account set up — send me your details whenever you're ready.")
        }
        onApplicationUpdate={(app) => patch({ application: app })}
        onNeedsGuardian={() => navigate('/guardian')}
        onReadyForReview={() => navigate('/review')}
        onSubmitted={() => navigate('/under-review')}
      />
    </PhoneScreen>
  );
}
