import { formatMoney as displayMoney } from "./money";

export type CheckoutRequest = {
  subtotal: number;
  region: string;
  currency: string;
};

export function submitOrder(request: CheckoutRequest): string {
  return displayMoney(request.subtotal, request.currency);
}

export class CheckoutController {
  process(request: CheckoutRequest): string {
    return submitOrder(request);
  }
}
