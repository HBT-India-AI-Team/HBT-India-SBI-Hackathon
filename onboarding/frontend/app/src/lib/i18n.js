import { useCallback } from 'react';
import { useApp } from '../context/AppContext';

// Hindi translations for the onboarding flow.
//
// The English sentence IS the key. That is deliberate: it keeps the JSX
// readable (`t('Choose your language')` still says what it renders), it needs
// no key invented per string, and anything not yet translated falls back to
// the English it was keyed by rather than showing a missing-key marker. A
// string that never reaches this file is untranslated, not broken.
//
// HINDI ONLY. The picker offers six languages, but only Hindi is translated;
// Tamil, Telugu, Bengali and Marathi fall through to English, which is what
// they did before. Adding one later means adding a dictionary here and a line
// in DICTIONARIES -- no page needs to change.
//
// Register follows the same rule the FinGuru style guide uses: the Hindi
// people actually speak, which borrows English freely. "मोबाइल नंबर", not
// "चलभाष संख्या"; "अकाउंट" alongside "खाता". Product names, PAN, KYC, OTP,
// RBI, WhatsApp and SBI YONO stay as they are -- nobody translates those.
const HI = {
  // --- language picker ---
  'Choose your language': 'अपनी भाषा चुनें',
  'You can change this anytime in chat.': 'आप इसे चैट में कभी भी बदल सकते हैं।',
  Continue: 'आगे बढ़ें',

  // --- product confirmation ---
  'Open Account': 'अकाउंट खोलें',
  "Based on what you're looking for, here are the accounts we can open for you today. Pick one to continue.":
    'आपकी जरूरत के हिसाब से, ये अकाउंट हम आज आपके लिए खोल सकते हैं। आगे बढ़ने के लिए एक चुनें।',
  'Yes, this one': 'हाँ, यही',
  // Product cards. These live in the page, not the product catalog the API
  // serves, so translating them needs no backend change.
  Recommended: 'सुझाया गया',
  'For businesses': 'बिजनेस के लिए',
  'Guardian-operated': 'अभिभावक द्वारा संचालित',
  'Zero Balance Digital Savings Account': 'जीरो बैलेंस डिजिटल सेविंग्स अकाउंट',
  'MSME Current Account': 'MSME करंट अकाउंट',
  'Minor Savings Account': 'माइनर सेविंग्स अकाउंट',
  'No monthly maintenance fees': 'कोई मंथली मेंटेनेंस फीस नहीं',
  'Instant virtual debit card': 'तुरंत वर्चुअल डेबिट कार्ड',
  'Earn 3.5% interest p.a.': '3.5% सालाना ब्याज पाएँ',
  'GSTIN-linked business banking': 'GSTIN से जुड़ी बिजनेस बैंकिंग',
  'Free digital invoicing tools': 'मुफ्त डिजिटल इनवॉइसिंग टूल्स',
  'Dedicated relationship support': 'खास रिलेशनशिप सपोर्ट',
  'For under-18 account holders': '18 साल से कम उम्र वालों के लिए',
  'Guardian consent + co-verification': 'अभिभावक की सहमति और सह-सत्यापन',
  'Great for pocket-money & goals': 'पॉकेट मनी और छोटे लक्ष्यों के लिए बढ़िया',

  // --- greeting ---
  'Hey there': 'नमस्ते',
  'Welcome to YONO 3.0': 'YONO 3.0 में आपका स्वागत है',
  'Open a bank account in a few minutes — just chat with us, no forms, no branch visits.':
    'कुछ ही मिनटों में बैंक अकाउंट खोलें — बस हमसे चैट करें, न कोई फॉर्म, न ब्रांच के चक्कर।',
  "Let's get started": 'चलिए शुरू करें',
  'I already have an application': 'मेरा आवेदन पहले से है',

  // --- consent ---
  'Before we begin': 'शुरू करने से पहले',
  "To open your account we'll need to verify your mobile number, PAN and a few documents. We only use this information for KYC and account opening, in line with RBI guidelines.":
    'आपका अकाउंट खोलने के लिए हमें आपका मोबाइल नंबर, PAN और कुछ डॉक्यूमेंट वेरिफाई करने होंगे। यह जानकारी हम सिर्फ KYC और अकाउंट खोलने के लिए इस्तेमाल करते हैं, RBI के नियमों के मुताबिक।',
  'Mobile number': 'मोबाइल नंबर',
  'For OTP verification & account communication': 'OTP वेरिफिकेशन और अकाउंट से जुड़ी जानकारी के लिए',
  'PAN & ID documents': 'PAN और पहचान के डॉक्यूमेंट',
  'For identity verification (KYC)': 'पहचान वेरिफिकेशन (KYC) के लिए',
  'Account preferences': 'अकाउंट की पसंद',
  'To set up the right product for you': 'आपके लिए सही प्रोडक्ट चुनने के लिए',
  'Terms & Conditions': 'नियम और शर्तें',
  "I agree to SBI YONO 3.0's account opening terms, applicable service charges, and the schedule of fees for the selected product.":
    'मैं SBI YONO 3.0 की अकाउंट खोलने की शर्तों, लागू सर्विस चार्ज और चुने गए प्रोडक्ट की फीस से सहमत हूँ।',
  'Data usage & KYC sharing': 'डेटा का इस्तेमाल और KYC साझा करना',
  'I consent to SBI verifying my PAN, Aadhaar-linked KYC records and uploaded documents with relevant government/regulatory systems (CKYC, NSDL) for identity verification.':
    'मैं सहमति देता हूँ कि SBI मेरे PAN, आधार से जुड़े KYC रिकॉर्ड और अपलोड किए गए डॉक्यूमेंट को पहचान वेरिफिकेशन के लिए संबंधित सरकारी/नियामक सिस्टम (CKYC, NSDL) से जाँचे।',
  'Communication consent': 'संपर्क की सहमति',
  'I agree to receive account-related communication via SMS, WhatsApp, email and in-app notifications, including OTPs and status updates.':
    'मैं सहमत हूँ कि अकाउंट से जुड़ी जानकारी, OTP और स्टेटस अपडेट मुझे SMS, WhatsApp, ईमेल और ऐप नोटिफिकेशन के जरिए भेजी जाए।',

  // --- checklist items (RequirementsChecklist preview list) ---
  'Starting…': 'शुरू हो रहा है…',
  'Mobile number (OTP)': 'मोबाइल नंबर (OTP)',
  'PAN verification': 'PAN वेरिफिकेशन',
  'Upload PAN card photo': 'PAN कार्ड की फोटो अपलोड करें',
  'Confirm product': 'प्रोडक्ट कन्फर्म करें',
  'Signatory PAN': 'हस्ताक्षरकर्ता का PAN',
  'Business PAN': 'बिजनेस PAN',
  'GSTIN verification': 'GSTIN वेरिफिकेशन',
  'Authorized signatory details': 'अधिकृत हस्ताक्षरकर्ता की जानकारी',
  'Upload GST certificate': 'GST सर्टिफिकेट अपलोड करें',
  "Minor's mobile number (OTP)": 'नाबालिग का मोबाइल नंबर (OTP)',
  'Guardian consent': 'अभिभावक की सहमति',
  "Guardian's mobile number (OTP)": 'अभिभावक का मोबाइल नंबर (OTP)',
  'Upload guardian ID proof': 'अभिभावक का पहचान प्रमाण अपलोड करें',
  'Your data, your control': 'आपका डेटा, आपका नियंत्रण',
  'I agree, continue': 'मैं सहमत हूँ, आगे बढ़ें',
  'Not now': 'अभी नहीं',
  'Terms & consent': 'नियम और सहमति',
  'Please review and accept the following before we start your application.':
    'आपका आवेदन शुरू करने से पहले कृपया नीचे दी गई बातें पढ़कर स्वीकार करें।',
  'Accept & continue': 'स्वीकार करें और आगे बढ़ें',

  // --- requirements checklist ---
  "What you'll need": 'आपको क्या चाहिए होगा',
  'Quick overview before we dive in — you can complete these in any order the chat suggests.':
    'शुरू करने से पहले एक झलक — चैट जिस क्रम में कहे, आप इन्हें उसी क्रम में पूरा कर सकते हैं।',
  'Mobile number on file? (optional — lets us detect an existing/duplicate application for you)':
    'रिकॉर्ड में मोबाइल नंबर? (वैकल्पिक — इससे हम आपका पहले से मौजूद या डुप्लिकेट आवेदन पहचान सकते हैं)',
  "Let's start": 'चलिए शुरू करें',
  'Could not start your application. Please try again.':
    'आपका आवेदन शुरू नहीं हो सका। कृपया दोबारा कोशिश करें।',

  // --- chat ---
  'Onboarding Assistant': 'ऑनबोर्डिंग असिस्टेंट',
  'Get help': 'मदद लें',
  'View checklist': 'चेकलिस्ट देखें',
  'Type a message…': 'कोई संदेश लिखें…',
  'Nothing needed right now': 'अभी कुछ नहीं चाहिए',
  'A 6-digit code was sent (mocked in this sandbox — check the backend server console/log for the code).':
    '6 अंकों का कोड भेजा गया है (इस सैंडबॉक्स में यह मॉक है — कोड के लिए बैकएंड सर्वर का लॉग देखें)।',
  'Something went wrong reaching the server. Please try again.':
    'सर्वर से संपर्क करने में दिक्कत हुई। कृपया दोबारा कोशिश करें।',
  'Upload failed — please check the file and try again.':
    'अपलोड नहीं हो सका — कृपया फाइल जाँचकर दोबारा कोशिश करें।',
  'Requirements checklist': 'जरूरतों की चेकलिस्ट',
  'Uploaded: {name}': 'अपलोड किया गया: {name}',
  'Uploading…': 'अपलोड हो रहा है…',
  'Upload: {label}': 'अपलोड करें: {label}',
  'A specialist is reviewing {items}. We\'ll pick up where you left off as soon as that\'s done — you don\'t need to wait here.':
    '{items} की जाँच एक विशेषज्ञ कर रहे हैं। जैसे ही यह पूरा होगा, हम वहीं से आगे बढ़ेंगे — आपको यहाँ इंतजार करने की जरूरत नहीं।',
  "We're verifying {items} now. This usually takes a moment — you don't need to do anything.":
    'हम अभी {items} की जाँच कर रहे हैं। इसमें आमतौर पर थोड़ा ही समय लगता है — आपको कुछ करने की जरूरत नहीं।',
  'Demo outcome: verify': 'डेमो नतीजा: वेरिफाई',
  'Demo outcome: reject': 'डेमो नतीजा: अस्वीकार',
  'Demo outcome: default': 'डेमो नतीजा: डिफॉल्ट',
  'Demo control: forces the async review outcome for this upload':
    'डेमो कंट्रोल: इस अपलोड की जाँच का नतीजा तय करता है',
  Close: 'बंद करें',
  Back: 'वापस',

  // --- guardian ---
  'Guardian consent needed': 'अभिभावक की सहमति चाहिए',
  'This account needs a guardian': 'इस अकाउंट के लिए अभिभावक चाहिए',
  'Guardian mobile number': 'अभिभावक का मोबाइल नंबर',
  Relationship: 'रिश्ता',
  Parent: 'माता-पिता',
  'Legal guardian': 'कानूनी अभिभावक',
  'Guardian link generated (mock-sent):': 'अभिभावक लिंक बन गया (मॉक भेजा गया):',
  'Could not create guardian link.': 'अभिभावक लिंक नहीं बन सका।',
  'Could not open guardian session.': 'अभिभावक सेशन नहीं खुल सका।',
  'Guardian verification': 'अभिभावक सत्यापन',
  'Since this is a minor account, we need a parent/guardian to confirm consent and verify their own mobile number before we continue.':
    'चूँकि यह नाबालिग का अकाउंट है, आगे बढ़ने से पहले माता-पिता या अभिभावक को सहमति देनी होगी और अपना मोबाइल नंबर वेरिफाई करना होगा।',
  'Generating link…': 'लिंक बन रहा है…',
  'Send consent link to guardian': 'अभिभावक को सहमति लिंक भेजें',
  'Opening…': 'खुल रहा है…',
  'Continue as guardian now (demo)': 'अभी अभिभावक के तौर पर आगे बढ़ें (डेमो)',
  'In production the guardian opens this link on their own device — this button simulates that for the demo so the flow can be tested end-to-end in one browser.':
    'असल में अभिभावक यह लिंक अपने फोन पर खोलते हैं — यह बटन डेमो के लिए वही काम करता है, ताकि पूरा फ्लो एक ही ब्राउज़र में जाँचा जा सके।',

  // --- status labels ---
  'In progress': 'चल रहा है',
  'Under Review': 'समीक्षा में',
  Approved: 'मंजूर',
  'Ref:': 'संदर्भ:',
  'Hi! Please confirm your consent as the guardian on this account.':
    'नमस्ते! कृपया इस अकाउंट के अभिभावक के तौर पर अपनी सहमति दें।',

  // --- review & submit ---
  'Review & submit': 'समीक्षा करें और जमा करें',
  'Please double-check everything below. You can edit verified fields before submitting.':
    'कृपया नीचे दी गई सारी जानकारी दोबारा जाँच लें। जमा करने से पहले आप सत्यापित जानकारी बदल सकते हैं।',
  Edit: 'बदलें',
  Save: 'सेव करें',
  'Could not update this field.': 'यह जानकारी अपडेट नहीं हो सकी।',
  'Submitting…': 'जमा हो रहा है…',
  'Submit application': 'आवेदन जमा करें',
  'Uploaded & verified': 'अपलोड और वेरिफाई हो गया',
  // Requirement states as shown on the review card.
  NOT_STARTED: 'शुरू नहीं हुआ',
  AWAITING_INPUT: 'आपके जवाब का इंतजार',
  SUBMITTED: 'जमा हो गया',
  VERIFYING: 'जाँच हो रही है',
  VERIFIED: 'वेरिफाई हो गया',
  REJECTED: 'अस्वीकृत',
  ESCALATED: 'सपोर्ट के पास',
  'Submission failed. Please try again.': 'जमा नहीं हो सका। कृपया दोबारा कोशिश करें।',

  // Requirement labels and states. These come from the API, but translating
  // them here keeps the promise of no backend change -- the label is looked up
  // on the way to the screen, exactly like any other string.
  'Verify your mobile number': 'अपना मोबाइल नंबर वेरिफाई करें',
  'Upload PAN Card photo': 'PAN कार्ड की फोटो अपलोड करें',
  'Confirm product selection': 'चुना गया प्रोडक्ट कन्फर्म करें',
  'Submit application for review': 'आवेदन समीक्षा के लिए जमा करें',
  Pending: 'बाकी है',
  Submitted: 'जमा हो गया',
  'Verifying…': 'जाँच हो रही है…',
  Verified: 'वेरिफाई हो गया',
  'With support': 'सपोर्ट के पास',

  // --- under review / status ---
  "Thanks — we're reviewing your details now. This usually takes just a few seconds in this demo (1-2 business days in production).":
    'धन्यवाद — हम आपकी जानकारी की जाँच कर रहे हैं। इस डेमो में इसमें कुछ ही सेकंड लगते हैं (असल में 1-2 कामकाजी दिन)।',
  'Looks like you already have an approved account for this product':
    'लगता है इस प्रोडक्ट के लिए आपका अकाउंट पहले से मंजूर है',
  'No need to apply again.': 'दोबारा आवेदन करने की जरूरत नहीं।',

  // --- support options ---
  'Chat with a human agent': 'किसी एजेंट से चैट करें',
  'Escalate to our support team in-app': 'ऐप में ही हमारी सपोर्ट टीम तक पहुँचाएँ',
  'Request a call back': 'कॉल बैक मांगें',
  'Mocked call flow — no real telephony in this build':
    'यह कॉल फ्लो मॉक है — इस बिल्ड में असली टेलीफोनी नहीं है',
  'Get a deep link to continue this application there':
    'वहाँ यह आवेदन जारी रखने के लिए डीप लिंक पाएँ',
  'Application submitted!': 'आवेदन जमा हो गया!',
  'Track my application': 'मेरा आवेदन ट्रैक करें',
  'Application Status': 'आवेदन की स्थिति',
  'Track progress': 'प्रगति देखें',
  'Action needed': 'आपकी कार्रवाई चाहिए',
  'We need you to revisit a few things before we can continue:':
    'आगे बढ़ने से पहले हमें आपसे कुछ चीजें दोबारा देखने के लिए कहना है:',
  'Resolve now': 'अभी ठीक करें',
  'Need help with your application?': 'अपने आवेदन में मदद चाहिए?',
  'Contact Support': 'सपोर्ट से संपर्क करें',

  // --- duplicate ---
  "You're already with us!": 'आप पहले से हमारे साथ हैं!',
  'Application ref': 'आवेदन संदर्भ',
  Status: 'स्थिति',
  'View my account status': 'मेरे अकाउंट की स्थिति देखें',
  'Start a different application': 'दूसरा आवेदन शुरू करें',

  // --- support ---
  'Connect with support': 'सपोर्ट से जुड़ें',
  'How would you like to get help with your application?':
    'आप अपने आवेदन में किस तरह मदद लेना चाहेंगे?',
  'Start an application first to reach support about it.':
    'इसके बारे में सपोर्ट से बात करने के लिए पहले आवेदन शुरू करें।',
  Support: 'सपोर्ट',
  'Could not reach support right now. Please try again shortly.':
    'अभी सपोर्ट से संपर्क नहीं हो सका। कृपया थोड़ी देर बाद कोशिश करें।',
  'Thanks for the details — noted on your ticket. Our team will follow up on your registered mobile number shortly.':
    'जानकारी के लिए धन्यवाद — यह आपके टिकट में दर्ज कर ली गई है। हमारी टीम जल्द ही आपके रजिस्टर्ड मोबाइल नंबर पर संपर्क करेगी।',
  "You're connected. Ticket #{id} has been raised with our support team — a human agent will pick this up shortly.":
    'आप जुड़ गए हैं। टिकट #{id} हमारी सपोर्ट टीम के पास दर्ज हो गया है — कोई एजेंट जल्द ही इसे देखेगा।',
  'Support call': 'सपोर्ट कॉल',
  'Connecting…': 'कनेक्ट हो रहा है…',
  'Call ended': 'कॉल समाप्त',
  'End call': 'कॉल काटें',
  'YONO Support (live)': 'YONO सपोर्ट (लाइव)',
  'SBI Support': 'SBI सपोर्ट',
  'Unmute microphone': 'माइक चालू करें',
  'Mute microphone': 'माइक बंद करें',
  'Back to my application': 'मेरे आवेदन पर वापस',

  // --- whatsapp handoff ---
  'Continue on WhatsApp': 'WhatsApp पर जारी रखें',
  'Pick up where you left off': 'जहाँ छोड़ा था वहीं से शुरू करें',
  'Deep link': 'डीप लिंक',
  'Open in WhatsApp': 'WhatsApp में खोलें',
  'We generated a secure deep link that resumes this exact application inside WhatsApp.':
    'हमने एक सुरक्षित डीप लिंक बनाया है जो इसी आवेदन को WhatsApp में वहीं से आगे बढ़ाता है।',
  'Expires in {mins} minutes': '{mins} मिनट में खत्म हो जाएगा',
  'Could not create WhatsApp handoff link.': 'WhatsApp हैंडऑफ लिंक नहीं बन सका।',

  // --- success ---
  "You're all set!": 'सब तैयार है!',
  'Your account has been approved. Welcome to the SBI YONO family — you can now log in and start banking.':
    'आपका अकाउंट मंजूर हो गया है। SBI YONO परिवार में आपका स्वागत है — अब आप लॉगिन करके बैंकिंग शुरू कर सकते हैं।',
  'Go to YONO Home': 'YONO होम पर जाएँ',

  // --- chat replies from the rule-based engine -------------------------
  // These arrive from the API already composed ("Got it, thanks. " + the next
  // prompt), so translateReply() below takes them apart before looking the
  // pieces up here. Keys match the templates in
  // backend/services/rule_based_engine.py.
  "Hi! Let's get your account set up — send me your details whenever you're ready.":
    'नमस्ते! चलिए आपका अकाउंट सेट करते हैं — जब तैयार हों, अपनी जानकारी भेज दीजिए।',
  "Hi {name}! Let's get your account set up — send me your details whenever you're ready.":
    'नमस्ते {name}! चलिए आपका अकाउंट सेट करते हैं — जब तैयार हों, अपनी जानकारी भेज दीजिए।',
  'Please share your 10-digit mobile number to get started.':
    'शुरू करने के लिए अपना 10 अंकों का मोबाइल नंबर बताइए।',
  "Please share the guardian's 10-digit mobile number.":
    'अभिभावक का 10 अंकों का मोबाइल नंबर बताइए।',
  'Please share your PAN (format: ABCDE1234F).': 'अपना PAN बताइए (फॉर्मेट: ABCDE1234F)।',
  'Please share your business PAN (format: ABCDE1234F).':
    'अपना बिजनेस PAN बताइए (फॉर्मेट: ABCDE1234F)।',
  'Please share your business GSTIN (format: 22AAAAA0000A1Z5).':
    'अपना बिजनेस GSTIN बताइए (फॉर्मेट: 22AAAAA0000A1Z5)।',
  "Please share the authorized signatory's full name.":
    'अधिकृत हस्ताक्षरकर्ता का पूरा नाम बताइए।',
  "Please share the guardian's relationship to the minor to record consent (e.g. 'parent').":
    "सहमति दर्ज करने के लिए बताइए कि अभिभावक का नाबालिग से क्या रिश्ता है (जैसे 'parent')।",
  'Please upload the required document: {label}.': 'यह डॉक्यूमेंट अपलोड कीजिए: {label}।',
  'Shall we proceed with this product? (yes/no)':
    'क्या हम इसी प्रोडक्ट के साथ आगे बढ़ें? (yes/no)',
  'Ready to submit your application for review? (yes/no)':
    'क्या आपका आवेदन समीक्षा के लिए जमा कर दें? (yes/no)',
  "We've sent a 6-digit code. Please enter it to verify.":
    'हमने 6 अंकों का कोड भेजा है। वेरिफाई करने के लिए उसे डालिए।',
  "You're all caught up! There's nothing pending right now.":
    'सब हो गया! अभी कुछ बाकी नहीं है।',
  'Got it, thanks.': 'ठीक है, धन्यवाद।',
  'Thanks -- that completes everything we need for now.':
    'धन्यवाद — फिलहाल हमें जो चाहिए था वह सब पूरा हो गया।',
  "That still doesn't look right, so we've flagged this for our support team to take a look. They'll reach out shortly.":
    'यह अब भी सही नहीं लग रहा, इसलिए हमने इसे अपनी सपोर्ट टीम को देखने के लिए भेज दिया है। वे जल्द संपर्क करेंगे।',
  "That didn't look right.": 'यह सही नहीं लगा।',
  'Please provide: {label}': 'यह दीजिए: {label}',
  // Document review messages composed in ChatWindow itself.
  'Thanks! "{label}" is being reviewed — this usually takes a few seconds in this demo.':
    'धन्यवाद! "{label}" की जाँच हो रही है — इस डेमो में इसमें कुछ ही सेकंड लगते हैं।',
  '"{label}" verified successfully.': '"{label}" सफलतापूर्वक वेरिफाई हो गया।',
  'We couldn\'t verify "{label}" after a couple of tries, so we\'ve flagged it for our support team to review manually.':
    'कुछ कोशिशों के बाद भी हम "{label}" वेरिफाई नहीं कर सके, इसलिए इसे सपोर्ट टीम को मैन्युअल जाँच के लिए भेज दिया है।',
  'We couldn\'t read "{label}" clearly — the image may be blurry or the wrong document. Please try uploading again.':
    '"{label}" साफ नहीं पढ़ा जा सका — फोटो धुंधली हो सकती है या गलत डॉक्यूमेंट है। कृपया दोबारा अपलोड कीजिए।',

  // --- format hints (input placeholder) ---
  Type: 'लिखें',
  '10-digit mobile number': '10 अंकों का मोबाइल नंबर',
  'full name': 'पूरा नाम',
  'image/pdf upload': 'image/pdf अपलोड',
  'yes/no': 'yes/no',
  'guardian mobile number + relationship': 'अभिभावक का मोबाइल नंबर + रिश्ता',
  "Verify your (minor's) mobile number": 'अपना (नाबालिग का) मोबाइल नंबर वेरिफाई करें',
  "Verify guardian's mobile number": 'अभिभावक का मोबाइल नंबर वेरिफाई करें',
  'Authorized signatory PAN verification': 'अधिकृत हस्ताक्षरकर्ता का PAN वेरिफिकेशन',
  'Business PAN verification': 'बिजनेस PAN वेरिफिकेशन',
  'Upload GST Certificate': 'GST सर्टिफिकेट अपलोड करें',
  'Upload Guardian ID proof': 'अभिभावक का पहचान प्रमाण अपलोड करें',

  // --- name prompt ---
  'What should I call you?': 'मैं आपको क्या कहकर बुलाऊँ?',
  'So FinGuru can pick up where you left off.':
    'ताकि FinGuru वहीं से आगे बढ़ सके जहाँ आपने छोड़ा था।',
  'Please enter a name.': 'कृपया नाम डालें।',
  'Your name': 'आपका नाम',
  Welcome: 'स्वागत है',
};

const DICTIONARIES = { hi: HI };

/**
 * Translate one string. Falls back to the English it was keyed by, so an
 * untranslated string renders as it always did.
 *
 * `vars` fills {placeholders}: t('Hi {name}', { name }).
 */
export function translate(text, language, vars) {
  const dict = DICTIONARIES[String(language || '').trim().toLowerCase()];
  let out = (dict && dict[text]) || text;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) out = out.split(`{${k}}`).join(String(v));
  }
  return out;
}

/**
 * Translate a chat reply from the rule-based engine.
 *
 * These do not arrive as whole sentences to look up: the engine builds them by
 * concatenation -- "Got it, thanks. " followed by the prompt for whatever is
 * next, or "That didn't look right (wrong_code). " followed by the same prompt.
 * Every combination as its own dictionary key would be the cross product of
 * prefixes and prompts, and would silently miss a pair. So this peels the
 * prefix off, translates it, and recurses on the rest.
 *
 * Anything it cannot take apart comes back untouched, in English -- the same
 * fallback the rest of this module uses.
 */
export function translateReply(text, language) {
  if (!text) return text;

  const direct = translate(text, language);
  if (direct !== text) return direct;                 // whole reply is a known one

  // "Got it, thanks. <next prompt>"
  const GOT_IT = 'Got it, thanks. ';
  if (text.startsWith(GOT_IT)) {
    return `${translate('Got it, thanks.', language)} ${translateReply(text.slice(GOT_IT.length), language)}`;
  }

  // "That didn't look right (<error>). <next prompt>" -- the bracketed error is
  // an internal code (wrong_code, invalid_format), so it is dropped rather than
  // shown to someone reading Hindi.
  const bad = text.match(/^That didn't look right \([^)]*\)\.\s*([\s\S]*)$/);
  if (bad) {
    return `${translate("That didn't look right.", language)} ${translateReply(bad[1], language)}`;
  }

  // Prompts carrying a requirement label, which itself needs translating.
  const doc = text.match(/^Please upload the required document: (.+)\.$/);
  if (doc) {
    return translate('Please upload the required document: {label}.', language, {
      label: translate(doc[1], language),
    });
  }
  const generic = text.match(/^Please provide: (.+)$/);
  if (generic) {
    return translate('Please provide: {label}', language, { label: translate(generic[1], language) });
  }

  return text;
}

/** Bound to whatever language the user picked (AppContext). */
export function useT() {
  const { language } = useApp();
  return useCallback((text, vars) => translate(text, language, vars), [language]);
}

/** translateReply(), bound to the picked language. */
export function useReplyT() {
  const { language } = useApp();
  return useCallback((text) => translateReply(text, language), [language]);
}

export default useT;
