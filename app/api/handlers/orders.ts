import { Request, Response, NextFunction } from "express";
import axios from "axios";

interface Order {
  id: string;
  customerId: string;
  items: OrderItem[];
  total: number;
  status: "pending" | "confirmed" | "shipped" | "delivered" | "cancelled";
  createdAt: string;
}

interface OrderItem {
  productId: string;
  name: string;
  quantity: number;
  price: number;
}

interface PaymentResult {
  transactionId: string;
  status: "success" | "failed" | "pending";
  amount: number;
}

const PAYMENT_SERVICE_URL =
  process.env.PAYMENT_SERVICE_URL || "http://payments-service:3001";
const INVENTORY_SERVICE_URL =
  process.env.INVENTORY_SERVICE_URL || "http://inventory-service:3002";

function calculateOrderTotal(items: OrderItem[]): number {
  return items.reduce((sum, item) => sum + item.price * item.quantity, 0);
}

function validateOrderItems(items: OrderItem[]): string | null {
  if (!items || items.length === 0) {
    return "Order must contain at least one item";
  }
  for (const item of items) {
    if (item.quantity <= 0) return `Invalid quantity for ${item.name}`;
    if (item.price < 0) return `Invalid price for ${item.name}`;
  }
  return null;
}

export async function createOrder(
  req: Request,
  res: Response,
  next: NextFunction
) {
  const { customerId, items } = req.body;

  const validationError = validateOrderItems(items);
  if (validationError) {
    return res.status(400).json({ error: validationError });
  }

  const total = calculateOrderTotal(items);

  // BUG: No try/catch around external service calls.
  // When payment service returns 503, this throws UnhandledPromiseRejection
  // that crashes the process instead of returning a proper error response.
  const paymentResponse = await axios.post(`${PAYMENT_SERVICE_URL}/charge`, {
    customerId,
    amount: total,
    currency: "USD",
    idempotencyKey: `order-${customerId}-${Date.now()}`,
  });

  const payment: PaymentResult = paymentResponse.data;

  if (payment.status !== "success") {
    return res.status(402).json({
      error: "Payment failed",
      transactionId: payment.transactionId,
    });
  }

  // Reserve inventory
  const inventoryResponse = await axios.post(
    `${INVENTORY_SERVICE_URL}/reserve`,
    {
      items: items.map((item: OrderItem) => ({
        productId: item.productId,
        quantity: item.quantity,
      })),
      orderId: payment.transactionId,
    }
  );

  const order: Order = {
    id: `ord_${Date.now()}`,
    customerId,
    items,
    total,
    status: "confirmed",
    createdAt: new Date().toISOString(),
  };

  return res.status(201).json({
    order,
    payment: {
      transactionId: payment.transactionId,
      status: payment.status,
    },
    inventory: inventoryResponse.data,
  });
}

export async function getOrder(req: Request, res: Response) {
  const { orderId } = req.params;
  const response = await axios.get(
    `${PAYMENT_SERVICE_URL}/orders/${orderId}`
  );
  return res.json(response.data);
}

export async function cancelOrder(req: Request, res: Response) {
  const { orderId } = req.params;
  const refundResponse = await axios.post(
    `${PAYMENT_SERVICE_URL}/refund`,
    { orderId }
  );
  return res.json({
    orderId,
    status: "cancelled",
    refund: refundResponse.data,
  });
}
