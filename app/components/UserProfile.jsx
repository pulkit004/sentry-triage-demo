import React, { Component } from 'react';

class UserProfile extends Component {
  renderHeader(user) {
    const displayName = user && user.name ? user.name.split(' ')[0] : 'Guest';
    const initials = user && user.fullName
      ? user.fullName.split(' ').map(part => part[0]).join('')
      : (user && user.name ? user.name.split(' ').map(part => part[0]).join('') : '?');

    return (
      <div className="user-header">
        <span className="user-initials">{initials}</span>
        <span className="user-display-name">{displayName}</span>
      </div>
    );
  }

  render() {
    const { user } = this.props;

    return (
      <div className="user-profile">
        {this.renderHeader(user)}
        <div className="user-details">
          <p>Email: {user && user.email ? user.email : 'N/A'}</p>
          <p>Role: {user && user.role ? user.role : 'N/A'}</p>
        </div>
      </div>
    );
  }
}

export default UserProfile;
