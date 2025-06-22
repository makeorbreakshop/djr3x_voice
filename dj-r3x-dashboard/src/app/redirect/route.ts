import { NextRequest, NextResponse } from 'next/server'

export async function GET(request: NextRequest) {
  const url = new URL(request.url)
  const code = url.searchParams.get('code')
  const error = url.searchParams.get('error')
  const state = url.searchParams.get('state')
  
  console.log('=== SPOTIFY REDIRECT PROXY ===')
  console.log('Received at 127.0.0.1:3000/redirect')
  console.log('Code:', code ? 'YES' : 'NO')
  console.log('Error:', error || 'NONE')
  console.log('Forwarding to localhost:3000...')
  
  // Build the redirect URL to localhost:3000 with all parameters
  const redirectUrl = new URL('http://localhost:3000')
  
  if (code) {
    redirectUrl.searchParams.set('code', code)
  }
  
  if (error) {
    redirectUrl.searchParams.set('error', error)
  }
  
  if (state) {
    redirectUrl.searchParams.set('state', state)
  }
  
  console.log('Redirecting to:', redirectUrl.toString())
  
  return NextResponse.redirect(redirectUrl.toString())
}