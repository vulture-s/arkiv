// Web3Forms access key (https://web3forms.com — enter the inbox address, the key
// arrives by email). It is meant to sit in client code; it can only submit to
// that inbox. Empty = every feedback form stays out of the build: the per-page
// block disappears and /feedback.html shows the mail address instead. A form
// that cannot deliver is worse than no form.
export const WEB3FORMS_KEY = '';
export const FEEDBACK_ENDPOINT = 'https://api.web3forms.com/submit';
export const FEEDBACK_MAIL = 'better@wafflehouse.com.tw';
