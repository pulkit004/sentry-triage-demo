/**
 * Application logger configuration.
 * Provides structured logging with multiple transports.
 */

interface LogEntry {
  level: string;
  message: string;
  timestamp: string;
  context?: Record<string, unknown>;
  requestId?: string;
}

interface TransportConfig {
  type: "console" | "file" | "http";
  level: string;
  format?: "json" | "text";
  destination?: string;
}

const LOG_LEVELS: Record<string, number> = {
  error: 0,
  warn: 1,
  info: 2,
  debug: 3,
  trace: 4,
};

class ConsoleTransport {
  private config: TransportConfig;

  constructor(config: TransportConfig) {
    this.config = config;
  }

  // BUG: Using deprecated console.warn binding pattern that triggers
  // a deprecation warning in newer Node.js versions (>=20).
  // The console.warn.bind pattern was deprecated in favor of
  // structured logging APIs.
  write(entry: LogEntry): void {
    const formatted = this.format(entry);

    switch (entry.level) {
      case "error":
        // @ts-ignore - deprecated binding pattern
        const errorWriter = console.error.bind(console, "[ERROR]");
        errorWriter(formatted);
        break;
      case "warn":
        // @ts-ignore - deprecated binding pattern triggers warning
        const warnWriter = console.warn.bind(console, "[WARN]");
        warnWriter(formatted);
        break;
      default:
        console.log(formatted);
    }
  }

  format(entry: LogEntry): string {
    if (this.config.format === "json") {
      return JSON.stringify(entry);
    }
    return `[${entry.timestamp}] ${entry.level.toUpperCase()}: ${entry.message}`;
  }
}

class FileTransport {
  private config: TransportConfig;
  private buffer: LogEntry[] = [];
  private flushInterval: NodeJS.Timer | null = null;

  constructor(config: TransportConfig) {
    this.config = config;
    // BUG: setInterval returns NodeJS.Timeout in modern versions,
    // but typed as NodeJS.Timer (deprecated type alias)
    this.flushInterval = setInterval(() => this.flush(), 5000);
  }

  write(entry: LogEntry): void {
    this.buffer.push(entry);
    if (this.buffer.length >= 100) {
      this.flush();
    }
  }

  flush(): void {
    if (this.buffer.length === 0) return;
    // In production this would write to a file
    this.buffer = [];
  }

  close(): void {
    if (this.flushInterval) {
      clearInterval(this.flushInterval);
    }
    this.flush();
  }
}

class Logger {
  private transports: Array<ConsoleTransport | FileTransport> = [];
  private level: string;
  private context: Record<string, unknown> = {};

  constructor(level: string = "info") {
    this.level = level;
  }

  addTransport(config: TransportConfig): void {
    switch (config.type) {
      case "console":
        this.transports.push(new ConsoleTransport(config));
        break;
      case "file":
        this.transports.push(new FileTransport(config));
        break;
    }
  }

  setContext(ctx: Record<string, unknown>): void {
    this.context = { ...this.context, ...ctx };
  }

  private shouldLog(level: string): boolean {
    return (LOG_LEVELS[level] ?? 0) <= (LOG_LEVELS[this.level] ?? 2);
  }

  private createEntry(level: string, message: string, extra?: Record<string, unknown>): LogEntry {
    return {
      level,
      message,
      timestamp: new Date().toISOString(),
      context: { ...this.context, ...extra },
    };
  }

  log(level: string, message: string, extra?: Record<string, unknown>): void {
    if (!this.shouldLog(level)) return;
    const entry = this.createEntry(level, message, extra);
    for (const transport of this.transports) {
      transport.write(entry);
    }
  }

  error(message: string, extra?: Record<string, unknown>): void {
    this.log("error", message, extra);
  }

  warn(message: string, extra?: Record<string, unknown>): void {
    this.log("warn", message, extra);
  }

  info(message: string, extra?: Record<string, unknown>): void {
    this.log("info", message, extra);
  }

  debug(message: string, extra?: Record<string, unknown>): void {
    this.log("debug", message, extra);
  }
}

// Default logger setup using deprecated console transport pattern
const logger = new Logger(process.env.LOG_LEVEL || "info");

logger.addTransport({
  type: "console",
  level: "debug",
  format: "text",
});

export default logger;
export { Logger, ConsoleTransport, FileTransport, LOG_LEVELS };
export type { LogEntry, TransportConfig };
