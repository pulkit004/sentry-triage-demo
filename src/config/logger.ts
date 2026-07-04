import { createLogger, format, transports, Logger } from 'winston';

const { combine, timestamp, printf, colorize, errors } = format;

const logLevel = process.env.LOG_LEVEL || 'info';

const logFormat = printf(({ level, message, timestamp: ts, stack, ...meta }) => {
  const metaStr = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : '';
  return `${ts} [${level.toUpperCase()}]: ${stack || message}${metaStr}`;
});

const developmentFormat = combine(
  colorize({ all: true }),
  timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
  errors({ stack: true }),
  logFormat
);

const productionFormat = combine(
  timestamp(),
  errors({ stack: true }),
  format.json()
);

const isDevelopment = process.env.NODE_ENV !== 'production';

const logger: Logger = createLogger({
  level: logLevel,
  format: isDevelopment ? developmentFormat : productionFormat,
  transports: [
    new transports.Console({
      handleExceptions: true,
      handleRejections: true,
    }),
  ],
  exitOnError: false,
});

if (!isDevelopment) {
  logger.add(
    new transports.Console({
      format: productionFormat,
    })
  );
}

export const log = {
  error: (message: string, meta?: Record<string, unknown>): void => {
    logger.error(message, meta);
  },
  warn: (message: string, meta?: Record<string, unknown>): void => {
    logger.warn(message, meta);
  },
  info: (message: string, meta?: Record<string, unknown>): void => {
    logger.info(message, meta);
  },
  http: (message: string, meta?: Record<string, unknown>): void => {
    logger.http(message, meta);
  },
  debug: (message: string, meta?: Record<string, unknown>): void => {
    logger.debug(message, meta);
  },
  verbose: (message: string, meta?: Record<string, unknown>): void => {
    logger.verbose(message, meta);
  },
};

export default logger;
