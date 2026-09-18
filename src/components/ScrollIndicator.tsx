import { motion } from "framer-motion";
import { Mail } from "lucide-react";

interface ScrollIndicatorProps {
  onClick?: () => void;
}

export const ScrollIndicator = ({ onClick }: ScrollIndicatorProps) => {
  return (
    <motion.button
      onClick={onClick}
      className="fixed bottom-4 right-4 sm:bottom-8 sm:right-8 flex items-center gap-2 text-foreground/50 hover:text-foreground transition-colors bg-card/50 backdrop-blur-sm px-3 py-2 rounded-full border border-border/50"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: 1.5, duration: 0.5 }}
      aria-label="Contact us"
    >
      <motion.div
        animate={{ scale: [1, 1.1, 1] }}
        transition={{
          duration: 2,
          repeat: Infinity,
          ease: "easeInOut"
        }}
      >
        <Mail className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.5} />
      </motion.div>
      <span className="text-xs sm:text-sm font-medium">Contact</span>
    </motion.button>
  );
};
