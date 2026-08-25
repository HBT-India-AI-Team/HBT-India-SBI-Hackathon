import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { editRequirement, getApplication, postMessage } from '../api/client';
import { useT } from '../lib/i18n';

const EDITABLE_TYPES = ['pan', 'business_pan', 'gstin', 'authorized_signatory'];

export default function ReviewSubmit() {
  const t = useT();
  const navigate = useNavigate();
  const { applicationId, sessionId, patch } = useApp();
  const [application, setApplication] = useState(null);
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editValue, setEditValue] = useState('');
  const [error, setError] = useState(null);

  const load = async () => {
    const app = await getApplication(applicationId);
    setApplication(app);
    patch({ application: app });
  };

  useEffect(() => {
    if (applicationId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  if (!applicationId) {
    navigate('/');
    return null;
  }

  const saveEdit = async (req) => {
    setBusy(true);
    try {
      await editRequirement(applicationId, req.id, editValue);
      setEditingId(null);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || t('Could not update this field.'));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await postMessage(sessionId, 'yes');
      await load();
      if (res.application_status === 'UNDER_REVIEW' || res.application_status === 'APPROVED') {
        navigate('/under-review');
      } else {
        // review_submit rejected for some reason -- surface reason
        setError(res.reply_text);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || t('Submission failed. Please try again.'));
    } finally {
      setBusy(false);
    }
  };

  const displayReqs = (application?.requirements || []).filter((r) => r.type !== 'review_submit');

  return (
    <PhoneScreen
      title={t('Review & submit')}
      footer={
        <div>
          {error && <p className="text-error text-[12.5px] mb-2">{error}</p>}
          <PrimaryButton onClick={submit} disabled={busy}>
            {busy ? t('Submitting…') : t('Submit application')}
          </PrimaryButton>
        </div>
      }
    >
      <p className="text-on-surface-variant text-sm mb-4">
        {t('Please double-check everything below. You can edit verified fields before submitting.')}
      </p>
      <div className="flex flex-col gap-3">
        {displayReqs.map((r) => (
          <div key={r.id} className="bg-surface-lowest border border-outline-variant/30 rounded-xl p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[12px] text-on-surface-variant">{t(r.label)}</p>
                <p className="font-heading font-bold text-[14.5px] text-on-surface">
                  {r.type === 'document' ? (r.state === 'VERIFIED' ? t('Uploaded & verified') : t(r.state)) : r.value || '—'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={`text-[11px] font-bold px-2 py-1 rounded-full ${
                    r.state === 'VERIFIED'
                      ? 'bg-success-container text-success'
                      : r.state === 'REJECTED' || r.state === 'ESCALATED'
                      ? 'bg-error-container text-error'
                      : 'bg-surface-container text-on-surface-variant'
                  }`}
                >
                  {t(r.state)}
                </span>
                {EDITABLE_TYPES.includes(r.type) && r.state === 'VERIFIED' && (
                  <button
                    className="text-primary text-[12px] font-semibold underline"
                    onClick={() => {
                      setEditingId(r.id);
                      setEditValue(r.value || '');
                    }}
                  >
                    {t('Edit')}
                  </button>
                )}
              </div>
            </div>
            {editingId === r.id && (
              <div className="mt-3 flex gap-2">
                <input
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="flex-1 h-10 rounded-lg bg-surface-container-low border border-outline-variant px-3 text-[13px]"
                />
                <button
                  onClick={() => saveEdit(r)}
                  className="px-3 h-10 rounded-lg bg-primary text-on-primary text-[12px] font-bold"
                >
                  {t('Save')}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </PhoneScreen>
  );
}
