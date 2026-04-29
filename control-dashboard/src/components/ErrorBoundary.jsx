import { Component } from "react";
import PropTypes from "prop-types";

/**
 * Catches React render errors and shows a fallback UI instead of a blank screen.
 */
export class ErrorBoundary extends Component {
  state = { hasError: false, error: null };

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    if (typeof this.props.onError === "function") {
      this.props.onError(error, info);
    }
  }

  render() {
    if (this.state.hasError && this.state.error) {
      if (this.props.fallback) {
        return this.props.fallback(this.state.error);
      }
      return (
        <div className="error-boundary-fallback" style={fallbackStyle}>
          <h2 style={headingStyle}>Mission control error</h2>
          <p style={messageStyle}>{this.state.error?.message ?? "Something went wrong."}</p>
          <button
            type="button"
            style={buttonStyle}
            onClick={() => this.setState({ hasError: false, error: null })}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const fallbackStyle = {
  position: "fixed",
  inset: 0,
  background: "#050505",
  color: "#00f2ff",
  fontFamily: "monospace",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: "16px",
  padding: "24px",
};
const headingStyle = { margin: 0, fontSize: "18px", letterSpacing: "2px" };
const messageStyle = { margin: 0, color: "#888", maxWidth: "400px", textAlign: "center" };
const buttonStyle = {
  marginTop: "8px",
  padding: "10px 20px",
  background: "#00f2ff",
  color: "#000",
  border: "none",
  fontWeight: "bold",
  cursor: "pointer",
  fontFamily: "monospace",
};

ErrorBoundary.propTypes = {
  children: PropTypes.node.isRequired,
  fallback: PropTypes.func,
  onError: PropTypes.func,
};
