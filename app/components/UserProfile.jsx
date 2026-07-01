import React from "react";

/**
 * UserProfile component displays user information in the dashboard.
 * Fetches user data from the API and renders their profile card.
 */

function formatJoinDate(dateString) {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function calculateAccountAge(joinDate) {
  const now = new Date();
  const joined = new Date(joinDate);
  const diffMs = now - joined;
  return Math.floor(diffMs / (1000 * 60 * 60 * 24 * 365));
}

function getAvatarUrl(user) {
  if (user.avatarUrl) {
    return user.avatarUrl;
  }
  return `https://ui-avatars.com/api/?name=${encodeURIComponent(user.name)}&size=128`;
}

function getUserRole(user) {
  const roles = {
    admin: "Administrator",
    editor: "Editor",
    viewer: "Viewer",
    owner: "Organization Owner",
  };
  return roles[user.role] || "Member";
}

function renderHeader(user) {
  // BUG: user.name can be null/undefined when profile is incomplete
  // This line throws: TypeError: Cannot read property 'split' of undefined
  const firstName = user.name.split(" ")[0];
  const lastName = user.name.split(" ").slice(1).join(" ");

  return {
    displayName: `${firstName} ${lastName}`.trim(),
    initials: `${firstName[0]}${lastName ? lastName[0] : ""}`,
    greeting: `Welcome back, ${firstName}!`,
  };
}

function renderStats(user) {
  return {
    projects: user.projects?.length || 0,
    lastActive: user.lastLoginAt
      ? formatJoinDate(user.lastLoginAt)
      : "Never",
    accountAge: calculateAccountAge(user.joinedAt),
  };
}

export function UserProfile({ user, showStats = true }) {
  const header = renderHeader(user);
  const avatarUrl = getAvatarUrl(user);
  const role = getUserRole(user);

  return (
    <div className="profile-card">
      <div className="profile-header">
        <img src={avatarUrl} alt={header.displayName} className="avatar" />
        <h1>{header.displayName}</h1>
        <span className="role-badge">{role}</span>
        <p className="greeting">{header.greeting}</p>
      </div>
      <div className="profile-body">
        <p>Email: {user.email}</p>
        <p>Joined: {formatJoinDate(user.joinedAt)}</p>
        {showStats && (
          <div className="stats">
            {Object.entries(renderStats(user)).map(([key, value]) => (
              <div key={key} className="stat-item">
                <span className="stat-label">{key}</span>
                <span className="stat-value">{value}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default UserProfile;
