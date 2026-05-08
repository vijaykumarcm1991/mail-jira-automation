def should_send_resolution_email(ticket_data, has_customer_visible_resolution=False):
    """
    Determine if a resolution email should be sent to the customer.

    Only sends emails when:
    1. Ticket is resolved
    2. No resolution email has been sent yet
    3. There's actually a customer-visible resolution comment

    Args:
        ticket_data: Dictionary containing ticket information
        has_customer_visible_resolution: Whether there's a customer-visible resolution comment

    Returns:
        bool: True if resolution email should be sent
    """
    if ticket_data.get('status', '').lower() != 'resolved':
        return False

    if ticket_data.get('resolved_email_sent'):
        return False

    # Only send email if there's a actual customer-visible resolution comment
    return has_customer_visible_resolution