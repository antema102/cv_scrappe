import { Input } from '../ui/Input';
import { Select } from '../ui/Select';

interface SearchFiltersProps {
  search: string;
  country: string;
  sector: string;
  countries: string[];
  sectors: string[];
  onSearch: (v: string) => void;
  onCountry: (v: string) => void;
  onSector: (v: string) => void;
}

export function SearchFilters({
  search, country, sector,
  countries, sectors,
  onSearch, onCountry, onSector,
}: SearchFiltersProps) {
  return (
    <div className="flex flex-col sm:flex-row gap-3">
      <div className="flex-1">
        <Input
          icon
          placeholder="Rechercher une entreprise, secteur, pays…"
          value={search}
          onChange={e => onSearch(e.target.value)}
        />
      </div>
      <Select
        value={country}
        onChange={e => onCountry(e.target.value)}
        className="sm:w-44"
      >
        <option value="">Tous les pays</option>
        {countries.map(c => <option key={c} value={c}>{c}</option>)}
      </Select>
      <Select
        value={sector}
        onChange={e => onSector(e.target.value)}
        className="sm:w-56"
      >
        <option value="">Tous les secteurs</option>
        {sectors.map(s => <option key={s} value={s}>{s}</option>)}
      </Select>
    </div>
  );
}
