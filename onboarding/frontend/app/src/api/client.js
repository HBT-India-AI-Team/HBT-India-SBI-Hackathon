import axios from 'axios';

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

const client = axios.create({ baseURL: API_BASE });

// ---- Applications ----
export const startApplication = (payload) => client.post('/applications/start', payload).then((r) => r.data);
export const getApplication = (id) => client.get(`/applications/${id}`).then((r) => r.data);
export const getApplicationsByUser = (mobile) => client.get(`/applications/by-user/${mobile}`).then((r) => r.data);
export const getApplicationStatus = (id) => client.get(`/applications/${id}/status`).then((r) => r.data);
export const postConsent = (id, payload) => client.post(`/applications/${id}/consent`, payload).then((r) => r.data);
export const uploadDocument = (id, formData) =>
  client.post(`/applications/${id}/documents`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then((r) => r.data);
export const editRequirement = (id, requirementId, value) => {
  const form = new FormData();
  form.append('value', value);
  return client.post(`/applications/${id}/edit/${requirementId}`, form).then((r) => r.data);
};
export const getNotifications = (id) => client.get(`/applications/${id}/notifications`).then((r) => r.data);
export const createHandoff = (id, channel) => client.post(`/applications/${id}/handoff/${channel}`).then((r) => r.data);
export const escalateSupport = (id, payload) => client.post(`/applications/${id}/support/escalate`, payload).then((r) => r.data);
export const createGuardianLink = (id, payload) => client.post(`/applications/${id}/guardian/link`, payload).then((r) => r.data);

// ---- Sessions ----
export const postMessage = (sessionId, text, contentType = 'text') =>
  client.post(`/sessions/${sessionId}/message`, { text, content_type: contentType }).then((r) => r.data);
export const getSessionState = (sessionId) => client.get(`/sessions/${sessionId}/state`).then((r) => r.data);
export const callInitiate = (sessionId) => client.post(`/sessions/${sessionId}/call/initiate`).then((r) => r.data);
export const callEnd = (sessionId) => client.post(`/sessions/${sessionId}/call/end`).then((r) => r.data);

export default client;
