import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { useApp } from '../context/AppContext';
import { getApplication, getApplicationStatus } from '../api/client';
import { useT } from '../lib/i18n';

const STATUS_META = {
  IN_PROGRESS: { label: 'In progress', color: 'bg-primary-container/20 text-primary' },
  UNDER_REVIEW: { label: 'Under Review', color: 'bg-tertiary-container/20 text-tertiary' },
  ACTION_NEEDED: { label: 'Action needed', color: 'bg-error-container text-error' },
  APPROVED: { label: 'Approved', color: 'bg-success-container text-success' },
};

export default function TrackStatus() {
  const t = useT();
  const navigate = useNavigate();
  const { applicationId } = useApp();
  const [status, setStatus] = useState(null);
  const [application, setApplication] = useState(null);

  useEffect(() => {
    if (!applicationId) return;
    let stop = false;
    const poll = async () => {
      try {
        const [s, app] = await Promise.all([getApplicationStatus(applicationId), getApplication(applicationId)]);
        if (stop) return;
        setStatus(s);
        setApplication(app);
        if (s.status === 'APPROVED') {
          setTimeout(() => !stop && navigate('/success'), 1500);
        }
      } catch (e) {
        console.error(e);
      }
    };
    poll();
    const id = setInterval(poll, 4000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [applicationId, navigate]);

  if (!applicationId) {
    navigate('/');
    return null;
  }

  const meta = STATUS_META[status?.status] || STATUS_META.IN_PROGRESS;
  const rejectedReqs = (application?.requirements || []).filter((r) => ['REJECTED', 'ESCALATED'].includes(r.state));

  return (
    <PhoneScreen title={t('Application Status')}>
      <section className="bg-surface-container-highest rounded-xl p-4 flex items-center justify-between mb-5">
        <div>
          <h2 className="font-heading font-bold text-lg text-on-surface">{t('Application Status')}</h2>
          <p className="text-[12px] text-on-surface-variant mt-1">{t('Ref:')} #{applicationId.slice(0, 8).toUpperCase()}</p>
        </div>
        <span className={`px-3 py-1.5 rounded-full text-[12px] font-bold ${meta.color}`}>{t(meta.label)}</span>
      </section>

      {status?.status === 'ACTION_NEEDED' && (
        <section className="bg-error-container rounded-xl p-4 mb-5">
          <h3 className="font-heading font-bold text-error text-[14.5px] mb-2">{t('Action needed')}</h3>
          <p className="text-[13px] text-on-error-container mb-3">
            {t('We need you to revisit a few things before we can continue:')}
          </p>
          <ul className="flex flex-col gap-1 mb-3">
            {rejectedReqs.map((r) => (
              <li key={r.id} className="text-[12.5px] text-on-error-container">
                • {t(r.label)}
              </li>
            ))}
          </ul>
          <button
            onClick={() => navigate('/onboarding')}
            className="w-full h-11 rounded-full bg-error text-on-error font-heading font-bold text-[13px]"
          >
            {t('Resolve now')}
          </button>
        </section>
      )}

      <section className="bg-surface-lowest rounded-xl p-5 border border-outline-variant/20">
        <h3 className="font-heading font-bold text-[15px] text-on-surface mb-4">{t('Track progress')}</h3>
        <div className="flex flex-col gap-4">
          {(status?.progress?.steps || []).map((s) => (
            <div key={s.index} className="flex gap-3">
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 text-[11px] font-bold ${
                  s.status === 'complete'
                    ? 'bg-primary text-on-primary'
                    : s.status === 'action_needed'
                    ? 'bg-error text-on-error'
                    : s.status === 'in_progress'
                    ? 'border-2 border-primary text-primary'
                    : 'bg-surface-container-high border-2 border-outline-variant text-outline'
                }`}
              >
                {s.status === 'complete' ? '✓' : s.index}
              </div>
              <div>
                <h4 className="text-[13px] font-semibold text-on-surface">{t(s.label)}</h4>
                <p className="text-[12px] text-on-surface-variant capitalize">{s.status.replace('_', ' ')}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="text-center mt-5">
        <p className="text-[13px] text-on-surface-variant">{t('Need help with your application?')}</p>
        <button onClick={() => navigate('/support')} className="mt-1 text-primary text-[13px] font-bold underline">
          {t('Contact Support')}
        </button>
      </section>
    </PhoneScreen>
  );
}
