import React from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import './auth.css'; // Reusing auth shell for the landing page aesthetics

export default function LandingPage() {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return <div className="auth-page-shell" />;
  }

  // Auto redirect to dashboard if logged in
  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className="auth-page-shell">
      <div className="auth-panel" style={{ maxWidth: '600px', textAlign: 'center', padding: '40px' }}>
        <h1 style={{ fontSize: '36px', marginBottom: '16px', color: '#28242b', lineHeight: 1.2 }}>
          Welcome to Lumina
        </h1>
        <p style={{ fontSize: '18px', color: '#514c54', marginBottom: '32px', lineHeight: 1.5 }}>
          Your intelligent study assistant. Transform your learning materials into interactive study guides, practice quizzes, and personalized tutoring sessions.
        </p>
        
        <div style={{ display: 'flex', gap: '16px', justifyContent: 'center' }}>
          <Link 
            to="/login" 
            className="primary-button" 
            style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
          >
            Sign In
          </Link>
          <Link 
            to="/register" 
            className="secondary-button" 
            style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
          >
            Create Account
          </Link>
        </div>
      </div>
    </div>
  );
}
