import { Request, Response } from 'express';
import axios, { AxiosError } from 'axios';

const PAYMENT_SERVICE_URL = process.env.PAYMENT_SERVICE_URL || 'http://127.0.0.1:3001';
const INVENTORY_SERVICE_URL = process.env.INVENTORY_SERVICE_URL || 'http://127.0.0.1:3002';

const axiosInstance = axios.create({
  timeout: 5000,
});

function isAxiosError(error: unknown): error is AxiosError {
  return axios.isAxiosError(error);
}

function handleServiceError(
  res: Response,
  error: unknown,
  serviceName: string
): void {
  if (isAxiosError(error)) {
    if (error.code === 'ECONNREFUSED' || error.code === 'ENOTFOUND' || error.code === 'ECONNRESET') {
      console.error(`[orders] ${serviceName} unavailable: ${error.message}`);
      res.status(503).json({
        error: 'Service Unavailable',
        message: `${serviceName} is currently unavailable. Please try again later.`,
      });
      return;
    }

    if (error.code === 'ETIMEDOUT' || error.code === 'ECONNABORTED') {
      console.error(`[orders] ${serviceName} request timed out: ${error.message}`);
      res.status(504).json({
        error: 'Gateway Timeout',
        message: `${serviceName} did not respond in time. Please try again later.`,
      });
      return;
    }

    if (error.response) {
      const status = error.response.status;
      const upstreamMessage =
        (error.response.data as { message?: string })?.message ||
        `${serviceName} returned an error.`;

      if (status >= 400 && status < 500) {
        console.error(`[orders] ${serviceName} client error ${status}: ${upstreamMessage}`);
        res.status(status).json({
          error: 'Upstream Client Error',
          message: upstreamMessage,
        });
        return;
      }

      console.error(`[orders] ${serviceName} server error ${status}: ${upstreamMessage}`);
      res.status(502).json({
        error: 'Bad Gateway',
        message: `${serviceName} encountered an internal error.`,
      });
      return;
    }
  }

  console.error(`[orders] Unexpected error communicating with ${serviceName}:`, error);
  res.status(500).json({
    error: 'Internal Server Error',
    message: 'An unexpected error occurred while processing your request.',
  });
}

export async function createOrder(req: Request, res: Response): Promise<void> {
  const { customerId, items, paymentDetails } = req.body;

  if (!customerId || !items || !Array.isArray(items) || items.length === 0) {
    res.status(400).json({ error: 'Bad Request', message: 'customerId and items are required.' });
    return;
  }

  if (!paymentDetails) {
    res.status(400).json({ error: 'Bad Request', message: 'paymentDetails are required.' });
    return;
  }

  // Step 1: Reserve inventory
  let inventoryReservation: { reservationId: string };
  try {
    const inventoryResponse = await axiosInstance.post(
      `${INVENTORY_SERVICE_URL}/reserve`,
      { items }
    );
    inventoryReservation = inventoryResponse.data;
  } catch (error) {
    handleServiceError(res, error, 'Inventory Service');
    return;
  }

  // Step 2: Process payment
  let paymentResult: { transactionId: string; status: string };
  try {
    const paymentResponse = await axiosInstance.post(
      `${PAYMENT_SERVICE_URL}/charge`,
      {
        customerId,
        paymentDetails,
        reservationId: inventoryReservation.reservationId,
        items,
      }
    );
    paymentResult = paymentResponse.data;
  } catch (error) {
    // Attempt to release the inventory reservation before responding
    try {
      await axiosInstance.post(`${INVENTORY_SERVICE_URL}/release`, {
        reservationId: inventoryReservation.reservationId,
      });
    } catch (releaseError) {
      console.error(
        '[orders] Failed to release inventory reservation after payment failure:',
        releaseError
      );
    }

    handleServiceError(res, error, 'Payment Service');
    return;
  }

  if (paymentResult.status !== 'success') {
    // Release inventory if payment was not successful
    try {
      await axiosInstance.post(`${INVENTORY_SERVICE_URL}/release`, {
        reservationId: inventoryReservation.reservationId,
      });
    } catch (releaseError) {
      console.error(
        '[orders] Failed to release inventory reservation after unsuccessful payment:',
        releaseError
      );
    }

    res.status(402).json({
      error: 'Payment Required',
      message: 'Payment was not successful. Please check your payment details and try again.',
    });
    return;
  }

  res.status(201).json({
    message: 'Order created successfully.',
    orderId: `ord_${Date.now()}`,
    transactionId: paymentResult.transactionId,
    reservationId: inventoryReservation.reservationId,
  });
}

export async function getOrderStatus(req: Request, res: Response): Promise<void> {
  const { orderId } = req.params;

  if (!orderId) {
    res.status(400).json({ error: 'Bad Request', message: 'orderId is required.' });
    return;
  }

  let paymentStatus: { status: string; transactionId: string };
  try {
    const paymentResponse = await axiosInstance.get(
      `${PAYMENT_SERVICE_URL}/status/${orderId}`
    );
    paymentStatus = paymentResponse.data;
  } catch (error) {
    handleServiceError(res, error, 'Payment Service');
    return;
  }

  res.status(200).json({
    orderId,
    paymentStatus: paymentStatus.status,
    transactionId: paymentStatus.transactionId,
  });
}

export async function refundOrder(req: Request, res: Response): Promise<void> {
  const { orderId } = req.params;
  const { reason } = req.body;

  if (!orderId) {
    res.status(400).json({ error: 'Bad Request', message: 'orderId is required.' });
    return;
  }

  let refundResult: { refundId: string; status: string };
  try {
    const refundResponse = await axiosInstance.post(
      `${PAYMENT_SERVICE_URL}/refund`,
      { orderId, reason }
    );
    refundResult = refundResponse.data;
  } catch (error) {
    handleServiceError(res, error, 'Payment Service');
    return;
  }

  res.status(200).json({
    message: 'Refund processed successfully.',
    orderId,
    refundId: refundResult.refundId,
    status: refundResult.status,
  });
}
