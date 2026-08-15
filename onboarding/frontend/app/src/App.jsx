import { HashRouter, Routes, Route } from 'react-router-dom';
import { AppProvider } from './context/AppContext';

import Greeting from './pages/Greeting';
import LanguagePicker from './pages/LanguagePicker';
import ProductConfirmation from './pages/ProductConfirmation';
import ConsentMoment from './pages/ConsentMoment';
import ConsentLegalDetails from './pages/ConsentLegalDetails';
import RequirementsChecklist from './pages/RequirementsChecklist';
import ChatPage from './pages/ChatPage';
import GuardianConsent from './pages/GuardianConsent';
import GuardianChatPage from './pages/GuardianChatPage';
import ReviewSubmit from './pages/ReviewSubmit';
import UnderReview from './pages/UnderReview';
import TrackStatus from './pages/TrackStatus';
import DuplicateUser from './pages/DuplicateUser';
import SupportSheet from './pages/SupportSheet';
import SupportChat from './pages/SupportChat';
import SupportCall from './pages/SupportCall';
import WhatsAppHandoff from './pages/WhatsAppHandoff';
import Success from './pages/Success';
import Home from './pages/Home';
import Game from './pages/Game';
import FinGuruHome from './pages/FinGuruHome';
import FinGuruChat from './pages/FinGuruChat';
import FinGuruHistory from './pages/FinGuruHistory';

export default function App() {
  return (
    <AppProvider>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/welcome" element={<Greeting />} />
          <Route path="/language" element={<LanguagePicker />} />
          <Route path="/product" element={<ProductConfirmation />} />
          <Route path="/consent" element={<ConsentMoment />} />
          <Route path="/consent/legal" element={<ConsentLegalDetails />} />
          <Route path="/requirements" element={<RequirementsChecklist />} />
          <Route path="/onboarding" element={<ChatPage />} />
          <Route path="/guardian" element={<GuardianConsent />} />
          <Route path="/guardian/chat" element={<GuardianChatPage />} />
          <Route path="/review" element={<ReviewSubmit />} />
          <Route path="/under-review" element={<UnderReview />} />
          <Route path="/status" element={<TrackStatus />} />
          <Route path="/duplicate" element={<DuplicateUser />} />
          <Route path="/support" element={<SupportSheet />} />
          <Route path="/support/chat" element={<SupportChat />} />
          <Route path="/support/call" element={<SupportCall />} />
          <Route path="/support/whatsapp" element={<WhatsAppHandoff />} />
          <Route path="/success" element={<Success />} />
          <Route path="/home" element={<Home />} />
          <Route path="/game" element={<Game />} />
          <Route path="/finguru" element={<FinGuruHome />} />
          <Route path="/finguru/chat" element={<FinGuruChat />} />
          <Route path="/finguru/history" element={<FinGuruHistory />} />
        </Routes>
      </HashRouter>
    </AppProvider>
  );
}
