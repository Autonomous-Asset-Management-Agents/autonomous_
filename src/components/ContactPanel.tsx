import { motion, AnimatePresence } from "framer-motion";
import { X, Send, Mail, User, MessageSquare } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface ContactPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export const ContactPanel = ({ isOpen, onClose }: ContactPanelProps) => {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [sending, setSending] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim() || !message.trim()) return;

    setSending(true);
    
    // Open email client with pre-filled content
    const subject = encodeURIComponent(`AAA Trading Bot Inquiry from ${name}`);
    const body = encodeURIComponent(
      `Name: ${name}\nEmail: ${email}\n\nMessage:\n${message}`
    );
    window.open(`mailto:g.apeldorn@gmail.com?subject=${subject}&body=${body}`, "_blank");
    
    setSending(false);
    setSubmitted(true);
    
    // Reset after a delay
    setTimeout(() => {
      setName("");
      setEmail("");
      setMessage("");
      setSubmitted(false);
    }, 3000);
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, x: 100 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 100 }}
          transition={{ type: "spring", damping: 25, stiffness: 200 }}
          className="fixed right-0 top-0 h-full w-full sm:w-96 bg-card/95 backdrop-blur-md border-l border-border/50 z-50 flex flex-col"
        >
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-border/50">
            <div className="flex items-center gap-2">
              <Mail className="w-5 h-5 text-chart-portfolio" />
              <h2 className="font-display text-lg">Contact Us</h2>
            </div>
            <button
              onClick={onClose}
              className="p-2 hover:bg-muted/50 rounded-lg transition-colors"
              aria-label="Close contact panel"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-4">
            {submitted ? (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                className="text-center py-8"
              >
                <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-success/20 flex items-center justify-center">
                  <Send className="w-8 h-8 text-success" />
                </div>
                <h3 className="font-display text-xl mb-2">Message Ready!</h3>
                <p className="text-sm text-muted-foreground">
                  Your email client should have opened. Send the email to complete your inquiry.
                </p>
              </motion.div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="text-sm text-muted-foreground flex items-center gap-2 mb-2">
                    <User className="w-4 h-4" />
                    Name
                  </label>
                  <Input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Your name"
                    className="bg-muted/30 border-border/50"
                    required
                  />
                </div>

                <div>
                  <label className="text-sm text-muted-foreground flex items-center gap-2 mb-2">
                    <Mail className="w-4 h-4" />
                    Email
                  </label>
                  <Input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="your@email.com"
                    className="bg-muted/30 border-border/50"
                    required
                  />
                </div>

                <div>
                  <label className="text-sm text-muted-foreground flex items-center gap-2 mb-2">
                    <MessageSquare className="w-4 h-4" />
                    Message
                  </label>
                  <Textarea
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    placeholder="Your message to us :-)"
                    className="bg-muted/30 border-border/50 min-h-[120px] resize-none"
                    required
                  />
                </div>

                <Button
                  type="submit"
                  disabled={sending || !name.trim() || !email.trim() || !message.trim()}
                  className="w-full bg-chart-portfolio hover:bg-chart-portfolio/80 text-white"
                >
                  {sending ? (
                    "Opening email..."
                  ) : (
                    <>
                      <Send className="w-4 h-4 mr-2" />
                      Send Inquiry
                    </>
                  )}
                </Button>
              </form>
            )}

          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
