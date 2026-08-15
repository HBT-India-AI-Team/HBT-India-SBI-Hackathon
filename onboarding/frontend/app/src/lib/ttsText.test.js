// Runs with zero extra dependencies:  node --test src/lib/ttsText.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeForTTS, buildFinGuruSystemPrompt, TTS_FORMATTING_RULES } from './ttsText.js';

test('strips a leading numbered-list marker', () => {
  assert.equal(normalizeForTTS('1. Check your balance.'), 'Check your balance.');
  assert.equal(normalizeForTTS('2) Review the alert.'), 'Review the alert.');
});

test('strips leading bullet / dash markers', () => {
  assert.equal(normalizeForTTS('- Pay your EMI on time.'), 'Pay your EMI on time.');
  assert.equal(normalizeForTTS('• Start a SIP.'), 'Start a SIP.');
  assert.equal(normalizeForTTS('* Keep an emergency fund.'), 'Keep an emergency fund.');
});

test('removes brackets/parentheses but keeps the inner text', () => {
  assert.equal(normalizeForTTS('Your monthly (EMI) is fixed.'), 'Your monthly EMI is fixed.');
  assert.equal(normalizeForTTS('Use [KYC] documents.'), 'Use KYC documents.');
});

test('strips non-speech symbols while keeping speakable punctuation', () => {
  assert.equal(normalizeForTTS('**Important**: save 20%.'), 'Important: save 20%.');
  assert.equal(normalizeForTTS('Rate is ~7% and rising.'), 'Rate is 7% and rising.');
  assert.equal(normalizeForTTS('Section #3 | note.'), 'Section 3 note.');
});

test('does NOT mistake a decimal for a list marker', () => {
  assert.equal(normalizeForTTS('3.5 lakh is the limit.'), '3.5 lakh is the limit.');
});

test('collapses runs of whitespace and newlines into single spaces', () => {
  assert.equal(normalizeForTTS('First,   check.\n\nThen review.'), 'First, check. Then review.');
});

test('re-appends a period when trailing punctuation is missing', () => {
  assert.equal(normalizeForTTS('Start a SIP today'), 'Start a SIP today.');
  assert.equal(normalizeForTTS('Pay on time (always)'), 'Pay on time always.');
});

test('preserves existing sentence-final punctuation, including the danda', () => {
  assert.equal(normalizeForTTS('இது சரி।'), 'இது சரி।');
  assert.equal(normalizeForTTS('Is that clear?'), 'Is that clear?');
  assert.equal(normalizeForTTS('Great!'), 'Great!');
});

test('is idempotent on already-clean text', () => {
  const clean = 'First, check your balance. Then, review the alert.';
  assert.equal(normalizeForTTS(clean), clean);
  assert.equal(normalizeForTTS(normalizeForTTS(clean)), clean);
});

test('handles empty / whitespace-only input', () => {
  assert.equal(normalizeForTTS(''), '');
  assert.equal(normalizeForTTS('   '), '');
  assert.equal(normalizeForTTS(null), '');
});

test('buildFinGuruSystemPrompt appends TTS rules only when forTTS is true', () => {
  const plain = buildFinGuruSystemPrompt({ langName: 'English' });
  const spoken = buildFinGuruSystemPrompt({ langName: 'English', forTTS: true });
  assert.ok(!plain.includes(TTS_FORMATTING_RULES.trim()));
  assert.ok(spoken.includes('Never use bullet points'));
  assert.ok(spoken.startsWith('You are FinGuru'));
});

test('buildFinGuruSystemPrompt reflects language and colloquial flags', () => {
  const p = buildFinGuruSystemPrompt({ langName: 'Tamil', colloquial: true });
  assert.ok(p.includes('Reply in Tamil.'));
  assert.ok(p.includes('colloquial'));
});
