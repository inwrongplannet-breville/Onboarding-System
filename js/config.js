/**
 * Deployment settings.
 *
 * Its own file because the base URL is the one thing that changes per
 * environment, and it has no business being buried in the data access layer.
 * The value is the ApiBaseUrl output of the onboarding-system-dev stack:
 *
 *   sam deploy   (or)   aws cloudformation describe-stacks \
 *     --stack-name onboarding-system-dev \
 *     --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" --output text
 */
window.App = window.App || {};

App.API_BASE_URL = 'https://4w9q4450be.execute-api.eu-north-1.amazonaws.com/dev';
