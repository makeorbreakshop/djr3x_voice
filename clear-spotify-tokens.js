// Quick utility to clear all Spotify tokens from browser storage
// Run this in browser console to force fresh authentication

console.log('🧹 Clearing all Spotify tokens...');

// Clear the main SDK token storage
localStorage.removeItem('spotify-sdk');
console.log('✅ Cleared localStorage spotify-sdk');

// Clear any potential additional SDK storage keys
const potentialKeys = [
  'spotify-sdk',
  'spotify_token',
  'spotify_access_token', 
  'spotify_refresh_token',
  'spotify-auth',
  'spotify-session'
];

// Clear specific keys from both localStorage and sessionStorage
potentialKeys.forEach(key => {
  localStorage.removeItem(key);
  sessionStorage.removeItem(key);
});

// Clear any keys that contain spotify and token/auth/sdk patterns
Object.keys(localStorage).forEach(key => {
  if (key.toLowerCase().includes('spotify') && 
      (key.includes('token') || key.includes('auth') || key.includes('sdk'))) {
    localStorage.removeItem(key);
    console.log(`✅ Cleared localStorage key: ${key}`);
  }
});

Object.keys(sessionStorage).forEach(key => {
  if (key.toLowerCase().includes('spotify') && 
      (key.includes('token') || key.includes('auth') || key.includes('sdk'))) {
    sessionStorage.removeItem(key);
    console.log(`✅ Cleared sessionStorage key: ${key}`);
  }
});

console.log('🎯 All Spotify tokens cleared! Please refresh the page to force re-authentication.');
console.log('🔑 The new authentication will include the required "streaming" scope for Web Playback SDK.');